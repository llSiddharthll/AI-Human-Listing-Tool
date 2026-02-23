
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch
from llm_engine import GeminiLLMEngine
from main import agentic_input_collection, IDENTIFIER_FIELDS
from platforms.common import LLMDrivenPlatform

class MockBrowser:
    async def execute_llm_actions(self, page, instruction):
        print(f"Executing LLM Action: {instruction}")
    async def save_listing(self, page):
        pass

async def test_agentic_flow():
    from unittest.mock import AsyncMock
    mock_llm = MagicMock()
    mock_llm.analyze_conversation_state = AsyncMock()
    
    # Simulate a conversation
    # 1. User: "Update price on Amazon" -> Missing info (product)
    # 2. User: "For 'Natural Face Wash'" -> Complete
    
    mock_llm.analyze_conversation_state.side_effect = [
        {
            "is_complete": False, 
            "suggested_next_question": "Which product would you like to update?",
            "platform": "amazon",
            "operation": "edit_listing"
        },
        {
            "is_complete": True,
            "platform": "amazon",
            "operation": "edit_listing",
            "product_name": "Natural Face Wash",
            "sku": None
        }
    ]
    
    with patch('builtins.input', side_effect=["Update price on Amazon", "For 'Natural Face Wash'", "y"]):
        result = await agentic_input_collection(mock_llm)
        print(f"Collected Input: {result}")
        assert result["platform"] == "amazon"
        assert result["product_name"] == "Natural Face Wash"
        assert result["operation"] == "edit_listing"

async def test_name_based_search():
    print("\nTesting Name-based Search Instruction...")
    mock_browser = MockBrowser()
    platform = LLMDrivenPlatform(mock_browser)
    platform.name = "Amazon"
    
    mock_page = MagicMock()
    updates = {"price": 599, "target_title": "Natural Face Wash"}
    
    # Test case 1: SKU is UNSPECIFIED, name is provided
    with patch.object(platform, 'save_listing', side_effect=lambda page: None):
        await platform.edit_listing(mock_page, updates, sku="UNSPECIFIED")
        
        # Test case 2: SKU is provided
        await platform.edit_listing(mock_page, updates, sku="SKU123")

if __name__ == "__main__":
    asyncio.run(test_agentic_flow())
    asyncio.run(test_name_based_search())
    print("\nAll tests passed!")
