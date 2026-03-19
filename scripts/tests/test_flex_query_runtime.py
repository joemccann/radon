import importlib


def test_flex_query_module_imports_without_requests_dependency():
    module = importlib.import_module("trade_blotter.flex_query")
    assert hasattr(module, "FlexQueryFetcher")


def test_blotter_service_imports_without_requests_dependency():
    module = importlib.import_module("trade_blotter.blotter_service")
    assert hasattr(module, "FlexQueryFetcher")
