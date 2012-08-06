# Copyright (c) 2012 OpenStack, LLC.
#
# Licensed under the Apache License, Version 2.0 (the 'License');
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an 'AS IS' BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import urlparse

import routes as routes_mapper
import webob
import webob.dec
import webob.exc

from quantum.api.v2 import attributes
from quantum.api.v2 import base
from quantum.extensions import extensions
from quantum import manager
from quantum.openstack.common import cfg
from quantum import wsgi


LOG = logging.getLogger(__name__)
DEFAULT_COLLECTION_ACTIONS = ['index', 'create']
DEFAULT_MEMBER_ACTIONS = ['show', 'update', 'delete']
DEFAULT_REQUIREMENTS = {'id': attributes.UUID_PATTERN, 'format': 'xml|json'}


class Index(wsgi.Application):
    def __init__(self, resources):
        self.resources = resources

    @webob.dec.wsgify(RequestClass=wsgi.Request)
    def __call__(self, req):
        metadata = {'application/xml': {'attributes': {
                    'resource': ['name', 'collection'],
                    'link': ['href', 'rel']}}}

        layout = []
        for name, collection in self.resources.iteritems():
            href = urlparse.urljoin(req.path_url, collection)
            resource = {'name': name,
                        'collection': collection,
                        'links': [{'rel': 'self',
                                   'href': href}]}
            layout.append(resource)
        response = dict(resources=layout)
        content_type = req.best_match_content_type()
        body = wsgi.Serializer(metadata=metadata).serialize(response,
                                                            content_type)
        return webob.Response(body=body, content_type=content_type)


class APIRouter(wsgi.Router):

    @classmethod
    def factory(cls, global_config, **local_config):
        return cls(**local_config)

    def __init__(self, **local_config):
        mapper = routes_mapper.Mapper()
        plugin = manager.QuantumManager.get_plugin()

        ext_mgr = extensions.PluginAwareExtensionManager.get_instance()
        ext_mgr.extend_resources("2.0", attributes.RESOURCE_ATTRIBUTE_MAP)

        resources = {'network': {'collection_name': 'networks'},
                     'subnet': {'collection_name': 'subnets'},
                     'port': {'collection_name': 'ports'}}

        ext_mgr.add_resources("2.0", resources)
        mapper.connect('index', '/', controller=Index(resources))

        for resource_name, resource_info in resources.iteritems():
            params = attributes.RESOURCE_ATTRIBUTE_MAP.get(
                resource_info['collection_name'], dict())

            collection_actions = resource_info.get('collection_actions',
                                                   DEFAULT_COLLECTION_ACTIONS)
            member_actions = resource_info.get('member_actions',
                                               DEFAULT_MEMBER_ACTIONS)
            controller = base.create_resource(resource_info['collection_name'],
                                              resource_name,
                                              plugin, params)

            mapper.collection(resource_info['collection_name'],
                              resource_name,
                              controller=controller,
                              requirements=DEFAULT_REQUIREMENTS,
                              collection_actions=collection_actions,
                              member_actions=member_actions)

            if resource_name == "router":
                for action in ["add_router_interface"]:
                    conditions = dict(method=['PUT'])
                    path = "/routers/{router_id}/%s" % action
                    with mapper.submapper(controller=controller,
                                      action=action,
                                      conditions=conditions) as submap:
                        submap.connect(path)
                        submap.connect("%s{.format}" % path)

        super(APIRouter, self).__init__(mapper)
