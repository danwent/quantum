# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2012 OpenStack LLC
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import collections
import logging
import socket
import sys
import time
import uuid

import netaddr
from sqlalchemy.ext import sqlsoup

from quantum.agent.common import config
from quantum.agent.linux import interface
from quantum.agent.linux import iptables_manager
from quantum.agent.linux import ip_lib
from quantum.common import exceptions
from quantum.common import utils
from quantum.openstack.common import cfg
from quantum.openstack.common import importutils
from quantum.version import version_string
from quantumclient.v2_0 import client

LOG = logging.getLogger(__name__)

class L3NATAgent(object):

    OPTS = [
        cfg.StrOpt('admin_user'),
        cfg.StrOpt('admin_password'),
        cfg.StrOpt('admin_tenant_name'),
        cfg.StrOpt('auth_url'),
        cfg.StrOpt('auth_strategy', default='keystone'),
        cfg.StrOpt('auth_region'),
        cfg.StrOpt('root_helper', default='sudo'),
        cfg.StrOpt('interface_driver',
                   help="The driver used to manage the virtual interface."),
        cfg.IntOpt('polling_interval',
                   default=3,
                   help="The time in seconds between state poll requests.")
    ]

    def __init__(self, conf):
        self.conf = conf

        if not conf.interface_driver:
            LOG.error(_('You must specify an interface driver'))
        self.driver = importutils.import_object(conf.interface_driver, conf)
        self.polling_interval = conf.polling_interval

        #FIXME(danwent): re-using the same client will result in the
        # keystone token timing out after a period of time.  What to do
        # that is smarter than recreating the client each time?
        self.quantum_client = client.Client(
            username=self.conf.admin_user,
            password=self.conf.admin_password,
            tenant_name=self.conf.admin_tenant_name,
            auth_url=self.conf.auth_url,
            auth_strategy=self.conf.auth_strategy,
            auth_region=self.conf.auth_region
        )

        self.nat_helper = NATHelper()


    def daemon_loop(self):
        old_ports = set()
        while True:
            try:
                new_ports = set()
                #TODO(danwent): provide way to limit this to a single
                # router, for model where agent runs in dedicated VM
                for r in self.quantum_client.list_routers()['routers']:
                    ports = self.quantum_client.list_ports(device_id=r['id'])
                    for p in ports['ports']:
                        new_ports.add(p['id'])
                        #TODO(danwent): ensure router ports only have
                        # exactly one IP address
                        ip = p['fixed_ips'][0]
                        #TODO(danwent): slim down fields returned
                        subnet = self.quantum_client.show_subnet(
                                        ip['subnet_id'])['subnet']
                        prefixlen = netaddr.IPNetwork(subnet['cidr']).prefixlen
                        ip_cidr = "%s/%s" % (ip['ip_address'], prefixlen)
                        self.ensure_local_device(p['id'], ip_cidr,
                                                 p['mac_address'])
                # identify ports that have been removed,
                # and clean the associated devices.
                removed_ports = old_ports - new_ports
                for port_id in removed_ports:
                    self.remove_local_device(port_id)
                old_ports = new_ports
            except:
                LOG.exception("Error running l3_nat daemon_loop")

            time.sleep(self.polling_interval)

    def get_device_name(self, port_id):
        return ('tap' + port_id)[:self.driver.DEV_NAME_LEN]

    def remove_local_device(self, port_id):
        interface_name = self.get_device_name(port_id)
        if ip_lib.device_exists(interface_name):
            self.driver.unplug(interface_name)

    def ensure_local_device(self, port_id, ip_cidr, mac_address):
        interface_name = self.get_device_name(port_id)

        if not ip_lib.device_exists(interface_name):
            self.driver.plug(None, port_id, interface_name, mac_address)

        self.driver.init_l3(interface_name, [ip_cidr])
        self.nat_helper.add_network(ip_cidr)

    def destroy(self, network):
        self.driver.unplug(self.get_interface_name(network))


class NATHelper(object):

    def __init__(self):

        my_ip = "172.16.110.227"

        #FIXME(danwent): config values
        self.metadata_host = my_ip
        self.metadata_port = 8775
        self.routing_source_ip = my_ip
        self.dmz_cidr = []
        self.send_arp_for_ha = True
        self.root_helper = 'sudo'

        self.iptables_manager = iptables_manager.IptablesManager(
                                        root_helper=self.root_helper)

        self.init_host()



    def init_host(self):
        """Setup base IPtables forwarding rules and system config."""

        # enable forwarding
        utils.execute(['sysctl', '-w', 'net.ipv4.ip_forward=1'],
                      self.root_helper, check_exit_code=False)

#        self.iptables_manager.ipv4['nat'].add_rule('POSTROUTING',
#                                          '-s %(range)s -d %(range)s '
#                                          '-m conntrack ! --ctstate DNAT '
#                                          '-j ACCEPT' %
#                                          {'range': self.fixed_range})
#
        # metadata rules
        utils.execute(['ip', 'addr', 'add', '169.254.169.254/32',
             'scope', 'link', 'dev', 'lo'], self.root_helper,
             check_exit_code=False)

        self.iptables_manager.ipv4['nat'].add_rule('PREROUTING',
                                          '-s 0.0.0.0/0 -d 169.254.169.254/32 '
                                          '-p tcp -m tcp --dport 80 -j DNAT '
                                          '--to-destination %s:%s' %
                                          (self.metadata_host,
                                           self.metadata_port))

        self.iptables_manager.ipv4['filter'].add_rule('INPUT',
                                             '-s 0.0.0.0/0 -d %s '
                                             '-p tcp -m tcp --dport %s '
                                             '-j ACCEPT' %
                                             (self.metadata_host,
                                              self.metadata_port))
        self.iptables_manager.apply()

    def add_network(self, cidr):
        self.iptables_manager.ipv4['nat'].add_rule('snat',
                                          '-s %s -j SNAT --to-source %s' %
                                           (cidr,
                                            self.routing_source_ip))

        self.iptables_manager.ipv4['nat'].add_rule('POSTROUTING',
                                          '-s %s -d %s/32 -j ACCEPT' %
                                        (cidr, self.metadata_host))
        self.iptables_manager.apply()


    def add_floating_ip(self, floating_ip, fixed_ip, device):
        utils.execute(['ip', 'addr', 'add', str(floating_ip) + '/32',
                      'dev', device], self.root_helper)
        if self.send_arp_for_ha:
            utils.execute(['arping', '-U', floating_ip, '-A', '-I', device,
                           '-c', 1], self.root_helper)

        for chain, rule in self.floating_forward_rules(floating_ip, fixed_ip):
            self.iptables_manager.ipv4['nat'].add_rule(chain, rule)
        self.iptables_manager.apply()


    def remove_floating_ip(self, floating_ip, fixed_ip, device):
        utils.execute(['ip', 'addr', 'del', str(floating_ip) + '/32',
                      'dev', device], self.root_helper)

        for chain, rule in self.floating_forward_rules(floating_ip, fixed_ip):
            self.iptables_manager.ipv4['nat'].remove_rule(chain, rule)
        self.iptables_manager.apply()

    def floating_forward_rules(self, floating_ip, fixed_ip):
        return [('PREROUTING', '-d %s -j DNAT --to %s' %
                 (floating_ip, fixed_ip)),
                ('OUTPUT', '-d %s -j DNAT --to %s' %
                 (floating_ip, fixed_ip)),
                ('float-snat', '-s %s -j SNAT --to %s' %
                 (fixed_ip, floating_ip))]

#    def initialize_gateway_device(dev, network_ref):
#        if not network_ref:
#            return
#
#
#    # NOTE(vish): The ip for dnsmasq has to be the first address on the
#    #             bridge for it to respond to reqests properly
#    full_ip = '%s/%s' % (network_ref['dhcp_server'],
#                         network_ref['cidr'].rpartition('/')[2])
#    new_ip_params = [[full_ip, 'brd', network_ref['broadcast']]]
#    old_ip_params = []
#    out, err = _execute('ip', 'addr', 'show', 'dev', dev,
#                        'scope', 'global', self.root_helper)
#    for line in out.split('\n'):
#        fields = line.split()
#        if fields and fields[0] == 'inet':
#            ip_params = fields[1:-1]
#            old_ip_params.append(ip_params)
#            if ip_params[0] != full_ip:
#                new_ip_params.append(ip_params)
#    if not old_ip_params or old_ip_params[0][0] != full_ip:
#        old_routes = []
#        result = _execute('ip', 'route', 'show', 'dev', dev,
#                          self.root_helper)
#        if result:
#            out, err = result
#            for line in out.split('\n'):
#                fields = line.split()
#                if fields and 'via' in fields:
#                    old_routes.append(fields)
#                    _execute('ip', 'route', 'del', fields[0],
#                             'dev', dev, self.root_helper)
#        for ip_params in old_ip_params:
#            _execute(*_ip_bridge_cmd('del', ip_params, dev),
#                     self.root_helper, check_exit_code=[0, 2, 254])
#        for ip_params in new_ip_params:
#            _execute(*_ip_bridge_cmd('add', ip_params, dev),
#                     self.root_helper, check_exit_code=[0, 2, 254])
#
#        for fields in old_routes:
#            _execute('ip', 'route', 'add', *fields,
#                     self.root_helper)
#        if FLAGS.send_arp_for_ha:
#            _execute('arping', '-U', network_ref['dhcp_server'],
#                     '-A', '-I', dev,
#                     '-c', 1, self.root_helper, check_exit_code=False)
#    if(FLAGS.use_ipv6):
#        _execute('ip', '-f', 'inet6', 'addr',
#                 'change', network_ref['cidr_v6'],
#                 'dev', dev, self.root_helper)


def main():
    conf = config.setup_conf()
    conf.register_opts(L3NATAgent.OPTS)
    conf.register_opts(interface.OPTS)
    conf(sys.argv)
    config.setup_logging(conf)

    mgr = L3NATAgent(conf)
    mgr.daemon_loop()


if __name__ == '__main__':
    main()
