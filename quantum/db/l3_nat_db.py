
from sqlalchemy.orm import exc
import sqlalchemy as sa
from quantum.api.v2 import attributes
from quantum.common import utils
from quantum.db import model_base
from quantum.db import models_v2
from quantum.extensions import l3_nat

class Router(model_base.BASEV2, models_v2.HasId, models_v2.HasTenant):
    """Represents a v2 quantum router."""
    name = sa.Column(sa.String(255))
    status = sa.Column(sa.String(16))
    admin_state_up = sa.Column(sa.Boolean)

class FloatingIPNetwork(model_base.BASEV2):
    """Represents a network that floating IPs can be allocated from."""
    floating_network_id = sa.Column(sa.String(36), sa.ForeignKey('networks.id'),
                           nullable=False, primary_key=True)
    is_default = sa.Column(sa.Boolean, nullable=False)

class FloatingIP(model_base.BASEV2, models_v2.HasId, models_v2.HasTenant):
    """Represents a floating IP, which may or many not be
       allocated to a tenant, and may or many not be associated with a
       port and IP address.
    """
    floating_ip_address = sa.Column(sa.String(64), nullable=False)
    floating_network_id = sa.Column(sa.String(64), nullable=False)
    floating_port_id = sa.Column(sa.String(36), sa.ForeignKey('ports.id'),
                                 nullable=False)
    fixed_port_id = sa.Column(sa.String(36), sa.ForeignKey('ports.id'),
                              nullable=True)
    fixed_ip_address = sa.Column(sa.String(64), nullable=True)
    fixed_port_id = sa.Column(sa.String(36), sa.ForeignKey('ports.id'),
                              nullable=True)
    router_id = sa.Column(sa.String(36), sa.ForeignKey('routers.id'),
                              nullable=True)


class L3_NAT_db_mixin(object):
    """Mixin class to add L3/NAT router methods to db_plugin_base_v2"""

    def _get_router(self, context, id, verbose=None):
        try:
            router = self._get_by_id(context, Router, id, verbose=verbose)
        except exc.NoResultFound:
            raise l3_nat.RouterNotFound(router_id=id)
        except exc.MultipleResultsFound:
            LOG.error('Multiple routers match for %s' % id)
            raise l3_nat.RouterNotFound(router_id=id)
        return router

    def _make_router_dict(self, router, fields=None):
        res = {'id': router['id'],
               'name': router['name'],
               'tenant_id': router['tenant_id'],
               'admin_state_up': router['admin_state_up'],
               'status': router['status']}
        return self._fields(res, fields)

    def create_router(self, context, router):
        r = router['router']
        tenant_id = self._get_tenant_id_for_create(context, r)
        with context.session.begin():
            router_db = Router(tenant_id=tenant_id,
                            id=r.get('id', utils.str_uuid()),
                            name=r['name'],
                            admin_state_up=r['admin_state_up'],
                            status="ACTIVE")
            context.session.add(router_db)
        return self._make_router_dict(router_db)

    def update_router(self, context, id, router):
        r = router['router']
        with context.session.begin():
            router_db = self._get_router(context, id)
            router_db.update(r)
        return self._make_router_dict(router_db)

    def delete_router(self, context, id):
        with context.session.begin():
            router = self._get_router(context, id)

            filter = {'device_id': [id]}
            ports = self.get_ports(context, filters=filter)
            if ports:
                raise l3_nat.RouterInUse(router_id=id)

            context.session.delete(router)

    def get_router(self, context, id, fields=None, verbose=None):
        router = self._get_router(context, id, verbose=verbose)
        return self._make_router_dict(router, fields)

    def get_routers(self, context, filters=None, fields=None, verbose=None):
        return self._get_collection(context, Router,
                                    self._make_router_dict,
                                    filters=filters, fields=fields,
                                    verbose=verbose)

    def add_router_interface(self, context, router_id, interface_info):
        # make sure router exists
        router = self._get_router(context, router_id)

        if 'port_id' in interface_info:
            if 'subnet_id' in interface_info:
                raise Exception("cannot specify subnet-id and port-id")
            port = self._get_port(context, interface_info['port_id'])
            if port['device_id']:
                raise Exception("Port already in use, cannot add interface")
            fixed_ips = [ ip for ip in port['fixed_ips'] ]
            if len(fixed_ips) != 1:
                raise Exception("Router port must have exactly one IP")
            port.update({'device_id': router_id})
            return {'port_id': port['id'],
                    'subnet_id': port['fixed_ips'][0]['subnet_id']}
        elif 'subnet_id' in interface_info:
            #FIXME(danwent): handle non-gateway-ip case
            subnet = self._get_subnet(context, interface_info['subnet_id'])
            fixed_ip = {'ip_address': subnet['gateway_ip'],
                        'subnet_id': subnet['id']}
            port = self.create_port(context,
                             {'port':
                               {'network_id': subnet['network_id'],
                                'fixed_ips': [fixed_ip],
                                'mac_address': attributes.ATTR_NOT_SPECIFIED,
                                'admin_state_up': True,
                                'device_id': router_id,
                                'name': ''}})
            return {'port_id': port['id'],
                    'subnet_id': subnet['id']}

    def _get_floatingip(self, context, id, verbose=None):
        try:
            floatingip = self._get_by_id(context, FloatingIP, id,
                                         verbose=verbose)
        except exc.NoResultFound:
            raise l3_nat.FloatingIPNotFound(floatingip_id=id)
        except exc.MultipleResultsFound:
            LOG.error('Multiple floatingips match for %s' % id)
            raise l3_nat.FloatingIPNotFound(floatingip_id=id)
        return floatingip

    def _make_floatingip_dict(self, floatingip, fields=None):
        res = {'id': floatingip['id'],
               'tenant_id': floatingip['tenant_id'],
               'floating_ip_address': floatingip['floating_ip_address'],
               'floating_network_id': floatingip['floating_network_id'],
               'router_id': floatingip['router_id'],
               'port_id': floatingip['fixed_port_id'],
               'fixed_ip_address': floatingip['fixed_ip_address']
        }
        return self._fields(res, fields)

    def _get_router_for_internal_subnet(self, context, internal_port,
                                        internal_subnet_id):

        subnet_db = self._get_subnet(context, internal_subnet_id)
        if not subnet_db['gateway_ip']:
            raise Exception("Cannot add floating IP to port on subnet %s "
                            "which has no gateway_ip" % internal_subnet_id)
        #TODO(danwent): can do join, but cannot use standard F-K syntax?
        # just do it inefficiently for now

        port_qry = context.session.query(models_v2.Port)
        try:
            ports = port_qry.filter_by(network_id=internal_port['network_id'])
            for p in ports:
                #FIXME(danwent): how to take len of query result without
                # loading it?
                ips = [ ip['ip_address'] for ip in p['fixed_ips']]
                if len(ips) != 1:
                    continue
                fixed = p['fixed_ips'][0]
                if (fixed['ip_address'] == subnet_db['gateway_ip'] and
                        fixed['subnet_id'] == internal_subnet_id):
                    router_qry = context.session.query(Router)
                    router = router_qry.filter_by(id=p['device_id']).one()
                    #TODO(danwent): confirm that this router has a floating
                    # ip enabled gateway with support for this floating IP
                    # network.
                    return router['id']
        except exc.NoResultFound:
            pass
        raise Exception("Could not find router associated "
                        "with internal port %s" % internal_port['id'])

    def get_assoc_data(self, context, tenant_id, fip):
        """When a floating IP is associated with an internal port,
           we need to extract/determine some data associated with the
           internal port, including the internal_ip_address, and router_id.
           We also need to confirm that this internal port is owned by the
           tenant who owns the floating IP.
        """

        internal_port = self._get_port(context, fip['port_id'])
        if not internal_port['tenant_id'] == tenant_id:
            raise Exception("Port %s is associated with a different tenant"
                            " and therefore cannot be found to floating IP %s"
                            % (fip['port_id'], fip['id']))

        internal_subnet = None
        if 'fixed_ip_address' in fip:
            internal_ip_address = fip['fixed_ip_address']
            for ip, subnet_id in internal_port['fixed_ips'].iteritems():
                if ip == internal_ip_address:
                    internal_subnet_id = subnet_id
            if not internal_subnet_id:
                raise Exception("port %s does not have fixed ip %s" %
                                (internal_port['id'], internal_ip_address))
        else:
            ips = [ ip['ip_address'] for ip in internal_port['fixed_ips']]
            if len(ips) == 0:
                raise Exception("Cannot add floating IP to port %s that has"
                                " no fixed IP addresses" % internal_port['id'])
            if len(ips) > 1:
                raise Exception("Port %s has multiple fixed IPs.  Must provide"
                                " a specific IP when assigning a floating IP" %
                                internal_port['id'])
            internal_ip_address = internal_port['fixed_ips'][0]['ip_address']
            internal_subnet_id = internal_port['fixed_ips'][0]['subnet_id']

        router_id = self._get_router_for_internal_subnet(context,
                                                    internal_port,
                                                    internal_subnet_id)
        return (fip['port_id'], internal_ip_address, router_id)

    def _get_device_name(self, fip):
        return 'floating-' + fip['router_id'] if 'router_id' in fip else ''

    def create_floatingip(self, context, floatingip):
        fip = floatingip['floatingip']
        tenant_id = self._get_tenant_id_for_create(context, fip)

        #TODO(danwent): validate that network_id is valid floatingip-network
        internal_ip_address = None
        router_id = None
        port_id = None
        if fip['port_id']:
            port_id, internal_ip_address, router_id = get_assoc_data(context,
                                                                     tenant_id,
                                                                     fip)
        # NOTE(danwent): this external port is never exposed to the tenant.
        # it is used purely for internal system and admin use when
        # managing floating IPs.
        external_port = self.create_port(context,
                             {'port':
                               {'network_id': fip['floating_network_id'],
                                'mac_address': attributes.ATTR_NOT_SPECIFIED,
                                'fixed_ips': attributes.ATTR_NOT_SPECIFIED,
                                'admin_state_up': True,
                                'device_id': self._get_device_name(fip),
                                'name': ''}})
        floating_ip_address = external_port['fixed_ips'][0]['ip_address']


        #TODO(danwent): add extended 'floating_ips' attritbute to port obj
        # probably do this in the mixin class

        with context.session.begin():
            floatingip_db = FloatingIP(tenant_id=tenant_id,
                            id=utils.str_uuid(),
                            floating_network_id=fip['floating_network_id'],
                            floating_port_id=external_port['id'],
                            floating_ip_address=floating_ip_address,
                            fixed_ip_address=internal_ip_address,
                            fixed_port_id=port_id,
                            router_id=router_id
                            )
            context.session.add(floatingip_db)

        #TODO(danwent): notifications

        return self._make_floatingip_dict(floatingip_db)

    def update_floatingip(self, context, id, floatingip):
        fip = floatingip['floatingip']

        with context.session.begin():
            floatingip_db = self._get_floatingip(context, id)

            #TODO(danwent): handle allocation/deallocation
            update_dict = {}

            fip['tenant_id'] = floatingip_db['tenant_id']
            fip['id'] = id
            if 'port_id' in fip:
                if fip['port_id']:
                    port_id, internal_ip, router_id = self.get_assoc_data(
                                                    context,
                                                    floatingip_db['tenant_id'],
                                                    fip)
                else:
                    port_id = None
                    internal_ip = None
                    router_id = None
                update_dict = {'fixed_port_id': port_id,
                               'fixed_ip_address': internal_ip,
                               'router_id': router_id}
            elif 'fixed_ip_address' in fip:
                # its possible that the port stays the same, but just
                # the IP changes to another IP on the same port.
                # call get_assoc_data just for validation checks
                fip['port_id'] = floatingip_db['fixed_port_id']
                self.get_assoc_data(context, floating_db['tenant_id'], fip)
                update_dict = {'fixed_ip_address': fip['fixed_ip_address']}

            floatingip_db.update(update_dict)
        return self._make_floatingip_dict(floatingip_db)

    def delete_floatingip(self, context, id):
        with context.session.begin():
            floatingip = self._get_floatingip(context, id)
            context.session.delete(floatingip)

    def get_floatingip(self, context, id, fields=None, verbose=None):
        floatingip = self._get_floatingip(context, id, verbose=verbose)
        return self._make_floatingip_dict(floatingip, fields)

    def get_floatingips(self, context, filters=None, fields=None, verbose=None):
        return self._get_collection(context, FloatingIP,
                                    self._make_floatingip_dict,
                                    filters=filters, fields=fields,
                                    verbose=verbose)

