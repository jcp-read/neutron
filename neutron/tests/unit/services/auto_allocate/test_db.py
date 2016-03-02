# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import mock

from neutron.common import exceptions as n_exc
from neutron import context
from neutron.services.auto_allocate import db
from neutron.services.auto_allocate import exceptions
from neutron.tests.unit import testlib_api


class AutoAllocateTestCase(testlib_api.SqlTestCaseLight):

    def setUp(self):
        super(AutoAllocateTestCase, self).setUp()
        self.ctx = context.get_admin_context()
        self.mixin = db.AutoAllocatedTopologyMixin()
        self.mixin._l3_plugin = mock.Mock()

    def test__provision_external_connectivity_expected_cleanup(self):
        """Test that the right resources are cleaned up."""
        subnets = [
            {'id': 'subnet_foo_1', 'network_id': 'network_foo'},
            {'id': 'subnet_foo_2', 'network_id': 'network_foo'},
        ]
        with mock.patch.object(self.mixin, '_cleanup') as mock_cleanup:
            self.mixin.l3_plugin.create_router.return_value = (
                {'id': 'router_foo'})
            self.mixin.l3_plugin.add_router_interface.side_effect = (
                n_exc.BadRequest(resource='router', msg='doh!'))
            self.assertRaises(exceptions.AutoAllocationFailure,
                self.mixin._provision_external_connectivity,
                self.ctx, 'ext_net_foo', subnets, 'tenant_foo')
            # expect no subnets to be unplugged
            mock_cleanup.assert_called_once_with(
                self.ctx, network_id='network_foo',
                router_id='router_foo', subnets=[])

    def test_get_auto_allocated_topology_dry_run_happy_path_for_kevin(self):
        with mock.patch.object(self.mixin, '_check_requirements') as f:
            self.mixin.get_auto_allocated_topology(
                self.ctx, mock.ANY, fields=['dry-run'])
            self.assertEqual(1, f.call_count)

    def test_get_auto_allocated_topology_dry_run_bad_input(self):
        self.assertRaises(n_exc.BadRequest,
            self.mixin.get_auto_allocated_topology,
            self.ctx, mock.ANY, fields=['foo'])

    def test__check_requirements_fail_on_missing_ext_net(self):
        self.assertRaises(exceptions.AutoAllocationFailure,
            self.mixin._check_requirements, self.ctx, 'foo_tenant')

    def test__check_requirements_fail_on_missing_pools(self):
        with mock.patch.object(
            self.mixin, '_get_default_external_network'),\
            mock.patch.object(
                self.mixin, '_get_supported_subnetpools') as g:
            g.side_effect = n_exc.NotFound()
            self.assertRaises(exceptions.AutoAllocationFailure,
                self.mixin._check_requirements, self.ctx, 'foo_tenant')

    def test__check_requirements_happy_path_for_kevin(self):
        with mock.patch.object(
            self.mixin, '_get_default_external_network'),\
            mock.patch.object(
                self.mixin, '_get_supported_subnetpools'):
            result = self.mixin._check_requirements(self.ctx, 'foo_tenant')
            expected = {'id': 'dry-run=pass', 'tenant_id': 'foo_tenant'}
            self.assertEqual(expected, result)
