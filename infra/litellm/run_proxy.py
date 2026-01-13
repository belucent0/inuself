import sys
import os
import logging
from litellm.proxy.proxy_cli import run_server
import litellm

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CustomProxyRunner")

try:
    # Import custom handler
    sys.path.append("/app/custom")
    from custom_handler import prometheus_router

    logger.info("Registering custom provider map...")
    litellm.custom_provider_map = [
        {"provider": "prometheus-router", "custom_handler": prometheus_router}
    ]

    # Monkeypatch provider list to ensure validation passes
    if not hasattr(litellm, "provider_list"):
        litellm.provider_list = []
    
    if "prometheus-router" not in litellm.provider_list:
        litellm.provider_list.append("prometheus-router")
        logger.info("Added prometheus-router to litellm.provider_list")
        
    # Attempt to patch LlmProviders enum validation side-effect
    # Some versions of LiteLLM check `litellm.mk_llm_provider` or similar.
    # By adding to provider_list, we cover most dynamic checks.
    
except Exception as e:
    logger.error(f"Failed to register custom provider: {e}")
    sys.exit(1)

# Set arguments for the CLI
# Note: we ignore the command line args passed to python and force our config
sys.argv = ["litellm", "--config", "/app/config.yaml", "--port", "4000"]

logger.info("Starting LiteLLM Proxy Server...")
if __name__ == "__main__":
    run_server()
