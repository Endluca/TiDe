from app.dts_v2_domain_processor_router import DOMAIN_PROCESSOR_KEY_TYPES
from app.dts_v2_domain_runtime import build_dts_v2_domain_processor


def test_runtime_factory_owns_every_domain_dirty_key():
    router = build_dts_v2_domain_processor(
        cutover_coverage_identity={"fleet": "shadow-v2"}
    )
    assert frozenset(router._processors) == DOMAIN_PROCESSOR_KEY_TYPES
