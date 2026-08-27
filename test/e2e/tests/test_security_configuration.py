# Copyright Amazon.com Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"). You may
# not use this file except in compliance with the License. A copy of the
# License is located at
#
#	 http://aws.amazon.com/apache2.0/
#
# or in the "license" file accompanying this file. This file is distributed
# on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
# express or implied. See the License for the specific language governing
# permissions and limitations under the License.

"""Integration tests for the EMR SecurityConfiguration resource."""

import json
import time

import pytest

from acktest.k8s import resource as k8s
from acktest.resources import random_suffix_name
from e2e import service_marker, CRD_GROUP, CRD_VERSION, load_emr_resource

RESOURCE_PLURAL = "securityconfigurations"

CREATE_WAIT_AFTER_SECONDS = 10
DELETE_WAIT_AFTER_SECONDS = 10


@pytest.fixture(scope="function")
def security_configuration(emr_client):
    resource_name = random_suffix_name("ack-sec-config", 32)

    replacements = {
        "RESOURCE_NAME": resource_name,
    }
    resource_data = load_emr_resource(
        "security_configuration",
        additional_replacements=replacements,
    )

    ref = k8s.CustomResourceReference(
        CRD_GROUP, CRD_VERSION, RESOURCE_PLURAL,
        resource_name, namespace="default",
    )
    k8s.create_custom_resource(ref, resource_data)
    cr = k8s.wait_resource_consumed_by_controller(ref)

    assert cr is not None
    assert k8s.get_resource_exists(ref)

    yield ref, cr, resource_name

    # Teardown - tolerate the case where the test already deleted the resource
    if k8s.get_resource_exists(ref):
        _, deleted = k8s.delete_custom_resource(ref)
        assert deleted
        time.sleep(DELETE_WAIT_AFTER_SECONDS)


@service_marker
@pytest.mark.canary
class TestSecurityConfiguration:
    def test_create_delete(self, emr_client, security_configuration):
        ref, _, resource_name = security_configuration

        # Resource should reach Synced condition after creation
        assert k8s.wait_on_condition(ref, "ACK.ResourceSynced", "True", wait_periods=30)

        cr = k8s.get_resource(ref)
        assert cr is not None
        assert cr["spec"]["name"] == resource_name
        assert "configuration" in cr["spec"]
        assert "status" in cr
        assert "creationDateTime" in cr["status"]

        # Verify the security configuration exists in EMR directly
        resp = emr_client.describe_security_configuration(Name=resource_name)
        assert resp["Name"] == resource_name
        assert resp["SecurityConfiguration"] is not None

        # Verify the configuration applied in AWS semantically matches what we
        # submitted in spec.configuration. EMR re-serializes the configuration
        # JSON (whitespace / key ordering may differ), so compare parsed JSON
        # objects rather than raw strings.
        expected_config = json.loads(cr["spec"]["configuration"])
        actual_config = json.loads(resp["SecurityConfiguration"])
        assert expected_config == actual_config, (
            "applied security configuration does not match submitted spec.configuration:\n"
            f"expected: {expected_config}\n"
            f"actual:   {actual_config}"
        )

        # Delete the resource
        _, deleted = k8s.delete_custom_resource(ref)
        assert deleted
        time.sleep(DELETE_WAIT_AFTER_SECONDS)

        # Verify the security configuration no longer exists in EMR
        with pytest.raises(emr_client.exceptions.InvalidRequestException):
            emr_client.describe_security_configuration(Name=resource_name)
