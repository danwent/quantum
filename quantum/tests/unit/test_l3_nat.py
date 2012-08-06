
import contextlib
import copy
import json
import logging
import mock
import unittest
import webtest
from webob import exc
from quantum.db import db_base_plugin_v2
from quantum.db import l3_nat_db
from quantum import quantum_plugin_base_v2
from quantum import manager
from quantum.extensions import l3_nat
from quantum.extensions import extensions
from quantum.api.v2 import attributes
from quantum.api.v2 import base
from quantum.api.v2 import resource as wsgi_resource
from quantum.api.v2 import router
from quantum.common import config
from quantum.common import exceptions as q_exc
from quantum import context
from quantum.extensions.extensions import PluginAwareExtensionManager
from quantum.manager import QuantumManager
from quantum.openstack.common import cfg
from quantum.tests.unit import test_api_v2
from quantum.tests.unit import test_db_plugin

LOG = logging.getLogger(__name__)

_uuid = test_api_v2._uuid
_get_path = test_api_v2._get_path


class L3NatExtensionTestCase(unittest.TestCase):

    def setUp(self):
        #plugin = 'quantum.quantum_plugin_base_v2.QuantumPluginBaseV2'
        plugin = 'quantum.extensions.l3_nat.RouterPluginBase'

        # Ensure 'stale' patched copies of the plugin are never returned
        manager.QuantumManager._instance = None

        # Ensure existing ExtensionManager is not used
        extensions.PluginAwareExtensionManager._instance = None

        # Save the global RESOURCE_ATTRIBUTE_MAP
        self.saved_attr_map = {}
        for resource, attrs in attributes.RESOURCE_ATTRIBUTE_MAP.iteritems():
            self.saved_attr_map[resource] = attrs.copy()

        # Create the default configurations
        args = ['--config-file', test_api_v2.etcdir('quantum.conf.test')]
        config.parse(args=args)

        # Update the plugin and extensions path
        cfg.CONF.set_override('core_plugin', plugin)

        self._plugin_patcher = mock.patch(plugin, autospec=True)
        self.plugin = self._plugin_patcher.start()

        # Instantiate mock plugin and enable the V2attributes extension
        QuantumManager.get_plugin().supported_extension_aliases = (
                ["os-quantum-router"])

        api = router.APIRouter()
        self.api = webtest.TestApp(api)

    def tearDown(self):
        self._plugin_patcher.stop()
        self.api = None
        self.plugin = None
        cfg.CONF.reset()

        # Restore the global RESOURCE_ATTRIBUTE_MAP
        attributes.RESOURCE_ATTRIBUTE_MAP = self.saved_attr_map

    def test_router_create(self):
        router_id = _uuid()
        data = {'router': {'name': 'router1', 'admin_state_up': True,
                            'tenant_id': _uuid()}}
        return_value = copy.deepcopy(data['router'])
        return_value.update({'status': "ACTIVE", 'id': router_id})

        instance = self.plugin.return_value
        instance.create_router.return_value = return_value

        res = self.api.post_json(_get_path('routers'), data)

        instance.create_router.assert_called_with(mock.ANY,
                                                  router=data)
        self.assertEqual(res.status_int, exc.HTTPCreated.code)
        self.assertTrue('router' in res.json)
        router = res.json['router']
        self.assertEqual(router['id'], router_id)
        self.assertEqual(router['status'], "ACTIVE")
        self.assertEqual(router['admin_state_up'], True)


    def test_router_list(self):
        router_id = _uuid()
        return_value = [{'router': {'name': 'router1', 'admin_state_up': True,
                            'tenant_id': _uuid(), 'id': router_id}}]

        instance = self.plugin.return_value
        instance.get_routers.return_value = return_value

        res = self.api.get(_get_path('routers'))

        instance.get_routers.assert_called_with(mock.ANY, fields=mock.ANY,
                                                verbose=mock.ANY,
                                                filters=mock.ANY)
        self.assertEqual(res.status_int, exc.HTTPOk.code)

    def test_router_update(self):
        router_id = _uuid()
        update_data = {'router': {'admin_state_up': False}}
        return_value = {'name': 'router1', 'admin_state_up': False,
                        'tenant_id': _uuid(),
                        'status': "ACTIVE", 'id': router_id}

        instance = self.plugin.return_value
        instance.update_router.return_value = return_value

        res = self.api.put_json(_get_path('routers', id=router_id),
                                update_data)

        instance.update_router.assert_called_with(mock.ANY, router_id,
                                                  router=update_data)
        self.assertEqual(res.status_int, exc.HTTPOk.code)
        self.assertTrue('router' in res.json)
        router = res.json['router']
        self.assertEqual(router['id'], router_id)
        self.assertEqual(router['status'], "ACTIVE")
        self.assertEqual(router['admin_state_up'], False)

    def test_router_get(self):
        router_id = _uuid()
        return_value = {'name': 'router1', 'admin_state_up': False,
                        'tenant_id': _uuid(),
                        'status': "ACTIVE", 'id': router_id}

        instance = self.plugin.return_value
        instance.get_router.return_value = return_value

        res = self.api.get(_get_path('routers', id=router_id))

        instance.get_router.assert_called_with(mock.ANY, router_id,
                                               fields=mock.ANY,
                                               verbose=mock.ANY)
        self.assertEqual(res.status_int, exc.HTTPOk.code)
        self.assertTrue('router' in res.json)
        router = res.json['router']
        self.assertEqual(router['id'], router_id)
        self.assertEqual(router['status'], "ACTIVE")
        self.assertEqual(router['admin_state_up'], False)

    def test_router_delete(self):
        router_id = _uuid()

        res = self.api.delete(_get_path('routers', id=router_id))

        instance = self.plugin.return_value
        instance.delete_router.assert_called_with(mock.ANY, router_id)
        self.assertEqual(res.status_int, exc.HTTPNoContent.code)

    def test_router_add_interface(self):
        router_id = _uuid()
        subnet_id = _uuid()
        port_id = _uuid()
        interface_data = {'subnet-id': subnet_id}
        return_value = copy.deepcopy(interface_data)
        return_value['port-id'] = port_id

        instance = self.plugin.return_value
        instance.add_router_interface.return_value = return_value

        path = _get_path('routers', id=router_id, action="add_router_interface")
        res = self.api.put_json(path, interface_data)

        instance.add_router_interface.assert_called_with(mock.ANY, router_id,
                                                  interface_data)
        self.assertEqual(res.status_int, exc.HTTPOk.code)
        self.assertTrue('port-id' in res.json)
        self.assertEqual(res.json['port-id'], port_id)
        self.assertEqual(res.json['subnet-id'], subnet_id)


# This class is mainly useful for testing
class TestL3NatPlugin(db_base_plugin_v2.QuantumDbPluginV2,
                      l3_nat_db.L3_NAT_db_mixin):
    supported_extension_aliases = ["os-quantum-router"]

class L3NatDBTestCase(test_db_plugin.QuantumDbPluginV2TestCase):

    def setUp(self):
        super(L3NatDBTestCase, self).setUp(plugin=
              'quantum.tests.unit.test_l3_nat.TestL3NatPlugin')


    def _create_router(self, fmt, tenant_id, name=None, admin_state_up=None):
        data = {'router': {'tenant_id': tenant_id}}
        if name:
            data['router']['name'] = name
        if admin_state_up:
            data['router']['admin_state_up'] = admin_state_up
        router_req = self.new_create_request('routers', data, fmt)
        return router_req.get_response(self.api)

    @contextlib.contextmanager
    def router(self, name='router1', admin_status_up=True, fmt='json'):
        res = self._create_router(fmt, _uuid(), name=name,
                                       admin_state_up=admin_status_up)
        router = self.deserialize(fmt, res)
        yield router
        self._delete('routers', router['router']['id'])

    def test_router_crd_ops(self):
        with self.router() as r:
            req = self.new_list_request('routers')
            res = req.get_response(self.api)
            body = self.deserialize('json', res)
            self.assertEquals(len(body['routers']), 1)
            self.assertEquals(body['routers'][0]['id'], r['router']['id'])

            req = self.new_show_request('routers', r['router']['id'])
            res = req.get_response(self.api)
            body = self.deserialize('json', res)
            self.assertEquals(body['router']['id'], r['router']['id'])


        # post-delete, check that it is really gone
        req = self.new_list_request('routers')
        res = req.get_response(self.api)
        body = self.deserialize('json', res)
        self.assertEquals(len(body['routers']), 0)

        req = self.new_show_request('routers', r['router']['id'])
        res = req.get_response(self.api)
        self.assertEqual(res.status_int, exc.HTTPNotFound.code)

    def test_router_update(self):
        rname1 = "yourrouter"
        rname2 = "nachorouter"
        with self.router(name=rname1) as r:
            req = self.new_show_request('routers', r['router']['id'])
            res = req.get_response(self.api)
            body = self.deserialize('json', res)
            self.assertEquals(body['router']['name'], rname1)

            req = self.new_update_request('routers',
                                          {'router': {'name': rname2}},
                                          r['router']['id'])
            res = req.get_response(self.api)

            req = self.new_show_request('routers', r['router']['id'])
            res = req.get_response(self.api)
            body = self.deserialize('json', res)
            self.assertEquals(body['router']['name'], rname2)

    def test_router_add_interface_subnet(self):
        with self.router() as r:
            with self.subnet() as s:
                interface_data = {'subnet_id': s['subnet']['id']}
                req = self.new_action_request('routers', interface_data,
                                              r['router']['id'],
                                              "add_router_interface")
                res = req.get_response(self.api)
                body = self.deserialize('json', res)
                self.assertTrue('port_id' in body)

                # fetch port and confirm device_id
                req = self.new_show_request('ports', body['port_id'])
                res = req.get_response(self.api)
                body = self.deserialize('json', res)
                self.assertEquals(body['port']['device_id'], r['router']['id'])


    def test_router_add_interface_port(self):
        with self.router() as r:
            with self.port() as p:
                interface_data = {'port_id': p['port']['id']}
                req = self.new_action_request('routers', interface_data,
                                              r['router']['id'],
                                              "add_router_interface")
                res = req.get_response(self.api)
                body = self.deserialize('json', res)
                self.assertTrue('port_id' in body)
                self.assertEquals(body['port_id'], p['port']['id'])

                # fetch port and confirm device_id
                req = self.new_show_request('ports', p['port']['id'])
                res = req.get_response(self.api)
                body = self.deserialize('json', res)
                self.assertEquals(body['port']['device_id'], r['router']['id'])

    def _create_floatingip(self, fmt, network_id, port_id=None,
                            fixed_ip=None):
        data = {'floatingip': {'floating_network_id': network_id,
                               'tenant_id': self._tenant_id}}
        if port_id:
            data['floatingip']['port_id'] = port_id
            if fixed_ip:
                data['floatingip']['fixed_ip'] = fixed_ip
        floatingip_req = self.new_create_request('floatingips', data, fmt)
        return floatingip_req.get_response(self.api)

    @contextlib.contextmanager
    def floatingip(self, subnet=None, port_id=None,
                   fmt='json'):
        with self.subnet() as s:
            res = self._create_floatingip(fmt, s['subnet']['network_id'],
                                          port_id=port_id)
            floatingip = self.deserialize(fmt, res)
            yield floatingip
            self._delete('floatingips', floatingip['floatingip']['id'])

    @contextlib.contextmanager
    def floatingip_withassoc(self, subnet=None, tenant_id=_uuid(), fmt='json'):
        with self.port() as p:
            with self.floatingip(subnet=subnet, tenant_id=tenant_id,
                                 port_id=p['port']['id'], fmt=fmt) as fip:
                yield fip

    def test_floatingip_crd_ops(self):
        with self.floatingip() as fip:
            req = self.new_list_request('floatingips')
            res = req.get_response(self.api)
            body = self.deserialize('json', res)
            self.assertEquals(len(body['floatingips']), 1)
            self.assertEquals(body['floatingips'][0]['id'],
                              fip['floatingip']['id'])

            req = self.new_show_request('floatingips', fip['floatingip']['id'])
            res = req.get_response(self.api)
            body = self.deserialize('json', res)
            self.assertEquals(body['floatingip']['id'], fip['floatingip']['id'])


        # post-delete, check that it is really gone
        req = self.new_list_request('floatingips')
        res = req.get_response(self.api)
        body = self.deserialize('json', res)
        self.assertEquals(len(body['floatingips']), 0)

        req = self.new_show_request('floatingips', fip['floatingip']['id'])
        res = req.get_response(self.api)
        self.assertEqual(res.status_int, exc.HTTPNotFound.code)

    def test_floatingip_update(self):
        with self.port() as p:
            with self.router() as r:
                subnet_id = p['port']['fixed_ips'][0]['subnet_id']
                interface_data = {'subnet_id': subnet_id}
                req = self.new_action_request('routers', interface_data,
                                              r['router']['id'],
                                              "add_router_interface")
                res = req.get_response(self.api)
                self.assertEqual(res.status_int, exc.HTTPOk.code)
                with self.floatingip() as fip:
                    req = self.new_show_request('floatingips',
                                            fip['floatingip']['id'])
                    res = req.get_response(self.api)
                    body = self.deserialize('json', res)
                    self.assertEqual(res.status_int, exc.HTTPOk.code)
                    self.assertEquals(body['floatingip']['port_id'], None)
                    self.assertEquals(body['floatingip']['fixed_ip_address'], None)
                    print "show body = %s" % body
                    req = self.new_update_request('floatingips',
                                              {'floatingip':
                                                {'port_id': p['port']['id']}},
                                              fip['floatingip']['id'])
                    res = req.get_response(self.api)
                    self.assertEqual(res.status_int, exc.HTTPOk.code)

                    req = self.new_show_request('floatingips',
                                            fip['floatingip']['id'])
                    res = req.get_response(self.api)
                    self.assertEqual(res.status_int, exc.HTTPOk.code)
                    body = self.deserialize('json', res)
                    print "show body = %s" % body
                    self.assertEquals(body['floatingip']['port_id'],
                                  p['port']['id'])
                    self.assertEqual(body['floatingip']['fixed_ip_address'],
                                 p['port']['fixed_ips'][0]['ip_address'])

