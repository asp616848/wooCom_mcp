"""P14 provisioner pure parts: deployer Job manifest, Job state, per-store values, failure text, settings."""

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from wmg_gateway.provisioner.k8s import deployer_job, job_state
from wmg_gateway.provisioner.stores import REASON_MAX, failure_reason, store_values, woo_mcp_url
from wmg_gateway.settings import Settings


def _settings(base_env: dict[str, str], **extra: Any) -> Settings:
    return Settings(**{**base_env, "database_url": "postgresql://x@localhost/x", **extra})


def _job(action: Any) -> dict[str, Any]:
    return deployer_job(
        name="wmg-prov-paris-1",
        namespace="mcp-platform",
        image="wmg/deployer:t1",
        target="paris",
        action=action,
        values_env="prod",
        deadline_s=900,
    )


def test_provision_job_manifest() -> None:
    job = _job("provision")
    spec, pod = job["spec"], job["spec"]["template"]["spec"]
    assert job["metadata"] == {
        "name": "wmg-prov-paris-1",
        "namespace": "mcp-platform",
        "labels": {
            "app.kubernetes.io/name": "deployer",
            "app.kubernetes.io/part-of": "wmg",
            "wmg.dev/store": "paris",
            "wmg.dev/action": "provision",
        },
    }
    assert spec["backoffLimit"] == 1 and spec["podReplacementPolicy"] == "Failed"
    assert spec["activeDeadlineSeconds"] == 900 and spec["ttlSecondsAfterFinished"] == 3600
    # the deployer NetworkPolicy selects the Job's pods by these labels
    assert job["spec"]["template"]["metadata"]["labels"] == job["metadata"]["labels"]
    assert pod["serviceAccountName"] == "wmg-deployer" and pod["restartPolicy"] == "Never"
    assert pod["securityContext"]["runAsNonRoot"] is True
    (container,) = pod["containers"]
    assert container["args"] == ["provision-store.sh", "paris"]
    assert container["env"] == [{"name": "VALUES_ENV", "value": "prod"}]
    assert container["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }
    assert container["resources"]["limits"] == {"cpu": "500m", "memory": "256Mi"}
    assert {"name": "input", "mountPath": "/input", "readOnly": True} in container["volumeMounts"]
    assert {"name": "input", "secret": {"secretName": "wmg-prov-paris"}} in pod["volumes"]


def test_teardown_job_has_no_input() -> None:
    pod = _job("teardown")["spec"]["template"]["spec"]
    assert pod["containers"][0]["args"] == ["teardown-store.sh", "paris"]
    assert [v["name"] for v in pod["volumes"]] == ["tmp"]


def _cond(type_: str, status: str = "True", reason: str = "", message: str = "") -> Any:
    return SimpleNamespace(type=type_, status=status, reason=reason, message=message)


@pytest.mark.parametrize(
    ("conditions", "expected"),
    [
        (None, ("running", None)),
        ([_cond("SuccessCriteriaMet")], ("running", None)),
        ([_cond("Complete")], ("succeeded", None)),
        ([_cond("Failed", "False"), _cond("Complete")], ("succeeded", None)),
        (
            [_cond("Failed", reason="DeadlineExceeded", message="Job was active longer than specified")],
            ("failed", "DeadlineExceeded: Job was active longer than specified"),
        ),
    ],
)
def test_job_state(conditions: Any, expected: Any) -> None:
    assert job_state(SimpleNamespace(conditions=conditions)) == expected
    assert job_state(None) == ("running", None)


def test_store_values(base_env: dict[str, str]) -> None:
    settings = _settings(base_env, woo_mcp_image="ghcr.io/o/wmg-woo-mcp:abc123", store_scheme="https")
    assert store_values(settings, "paris", "paris.shop.1-2-3-4.sslip.io") == {
        "store": {"host": "paris.shop.1-2-3-4.sslip.io", "scheme": "https", "title": "Paris Store"},
        "existingSecret": "paris-store-credentials",
        "ingress": {"tls": {"secretName": "paris-shop-tls"}},
        "wooMcp": {"image": {"repository": "ghcr.io/o/wmg-woo-mcp", "tag": "abc123"}},
    }
    assert woo_mcp_url("paris") == "http://paris-woo-mcp.store-paris.svc.cluster.local:8080/mcp"


def test_failure_reason_is_capped() -> None:
    assert failure_reason("provisioning failed (X)", "") == "provisioning failed (X)"
    assert failure_reason("p", "  a\nb  ") == "p\na\nb"
    long = failure_reason("p", "x" * 5000)
    assert len(long) == REASON_MAX and long.endswith("…")


def test_provisioning_settings(base_env: dict[str, str]) -> None:
    off = _settings(base_env)
    assert off.provisioning_enabled() is False and off.store_host("paris") == "paris.shop.localhost"
    on = _settings(
        base_env, deployer_image="d:1", woo_mcp_image="w:1", store_host_template="{name}.shop.x.io"
    )
    assert on.provisioning_enabled() is True and on.store_host("tokyo") == "tokyo.shop.x.io"
    with pytest.raises(ValidationError):
        _settings(base_env, store_host_template="shop.x.io")  # must contain {name}
    with pytest.raises(ValidationError):
        _settings(base_env, store_values_env="staging")


def test_hosted_job_manifests() -> None:
    deploy = deployer_job(
        name="wmg-host-pin-1",
        namespace="mcp-platform",
        image="wmg/deployer:t1",
        target="pincode-hosted",
        action="deploy",
        values_env="local",
        deadline_s=600,
    )
    labels = deploy["metadata"]["labels"]
    assert labels["wmg.dev/hosted"] == "pincode-hosted" and "wmg.dev/store" not in labels
    pod = deploy["spec"]["template"]["spec"]
    assert pod["containers"][0]["args"] == ["deploy-hosted.sh", "pincode-hosted"]
    assert {"name": "HOSTED_NAMESPACE", "value": "mcp-hosted"} in pod["containers"][0]["env"]
    assert {"name": "input", "secret": {"secretName": "wmg-hosted-pincode-hosted"}} in pod["volumes"]
    remove = deployer_job(
        name="wmg-unhost-pin-1",
        namespace="mcp-platform",
        image="wmg/deployer:t1",
        target="pincode-hosted",
        action="remove",
        values_env="local",
        deadline_s=300,
    )
    rpod = remove["spec"]["template"]["spec"]
    assert rpod["containers"][0]["args"] == ["remove-hosted.sh", "pincode-hosted"]
    assert [v["name"] for v in rpod["volumes"]] == ["tmp"]
