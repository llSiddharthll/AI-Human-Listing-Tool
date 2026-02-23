import streamlit as st
import asyncio
import logging
from pathlib import Path
from typing import Any
from main import get_platform, ensure_credentials, sanitize_text
from llm_engine import GeminiLLMEngine
from browser_engine import BrowserEngine
from config.settings import Settings
from config.credentials import CredentialManager
from data_engine import load_product_data, get_product_image_paths

# Set up logging
logging.basicConfig(level=logging.INFO)
logging.getLogger("google_genai").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
LOGGER = logging.getLogger(__name__)

# Page config
st.set_page_config(page_title="AI Listing Agent", page_icon="🛍️", layout="wide")

st.title("🛍️ AI Human Listing Agent")
st.markdown("Automate your product listings on Amazon, Shopify, Flipkart, and Myntra using AI.")

# Initialize session state for chat and engine
if "messages" not in st.session_state:
    st.session_state.messages = []

if "listing_state" not in st.session_state:
    st.session_state.listing_state = {
        "is_complete": False,
        "platform": None,
        "operation": None,
        "sku": None,
        "product_name": None,
        "data_file_path": None,
        "images_folder_path": None,
        "suggested_next_question": None,
        "command": None
    }

if "processing" not in st.session_state:
    st.session_state.processing = False

if "otp_needed" not in st.session_state:
    st.session_state.otp_needed = False

if "otp_value" not in st.session_state:
    st.session_state.otp_value = None

# Sidebar for settings
with st.sidebar:
    st.header("Settings")
    settings = Settings.from_env()
    
    # Model Selector
    from llm_engine import MODEL_CANDIDATES, DEFAULT_MODEL
    selected_model = st.selectbox(
        "AI Model",
        options=MODEL_CANDIDATES,
        index=MODEL_CANDIDATES.index(settings.gemini_model) if settings.gemini_model in MODEL_CANDIDATES else 0,
        help="gemini-2.5-flash-lite is cheapest and best for listing tasks."
    )
    settings.gemini_model = selected_model
    
    st.info(f"Model: **{selected_model}**")
    st.markdown("[Check Gemini Quotas](https://aistudio.google.com/app/plan_and_billing)")
    
    if st.button("Clear Chat"):
        st.session_state.messages = []
        st.session_state.listing_state = {"is_complete": False}
        st.rerun()

    st.divider()
    st.header("Product Data")
    upload_tab, manual_tab = st.tabs(["Upload File", "Manual Entry"])
    
    with upload_tab:
        uploaded_file = st.file_uploader("Upload Product File", type=['csv', 'xlsx', 'json'])
        if uploaded_file is not None:
            temp_dir = Path("temp_uploads")
            temp_dir.mkdir(exist_ok=True)
            temp_path = temp_dir / uploaded_file.name
            with open(temp_path, "wb") as f:
                f.write(uploaded_file.getbuffer())
            st.session_state.listing_state["data_file_path"] = str(temp_path)
            st.success(f"Loaded {uploaded_file.name} (Will be cleaned after run)")
            
    with manual_tab:
        with st.form("manual_product_form"):
            prod_title = st.text_input("Title")
            prod_sku = st.text_input("SKU")
            prod_price = st.text_input("Price")
            prod_cat = st.text_input("Category")
            prod_brand = st.text_input("Brand")
            prod_desc = st.text_area("Description")
            
            if st.form_submit_button("Save Product"):
                if not prod_title:
                    st.error("Title is required")
                else:
                    manual_product = {
                        "title": prod_title,
                        "sku": prod_sku,
                        "price": prod_price,
                        "category": prod_cat,
                        "brand": prod_brand,
                        "description": prod_desc
                    }
                    temp_dir = Path("temp_uploads")
                    temp_dir.mkdir(exist_ok=True)
                    temp_path = temp_dir / "manual_product.json"
                    import json
                    temp_path.write_text(json.dumps([manual_product]))
                    st.session_state.listing_state["data_file_path"] = str(temp_path)
                    st.success("Manual product saved! (Will be cleaned after run)")


    if st.session_state.otp_needed:
        st.warning("🔐 2FA/OTP Required!")
        with st.form("otp_form"):
            otp_input = st.text_input("Enter Code")
            if st.form_submit_button("Submit OTP"):
                st.session_state.otp_value = otp_input
                st.info("OTP submitted. Resuming...")

# Display chat messages
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# LLM Engine Singleton
@st.cache_resource
def get_llm():
    return GeminiLLMEngine(api_keys=settings.gemini_api_keys, model=settings.gemini_model)

llm = get_llm()

async def run_listing_process(state):
    """Executes the actual browser automation."""
    async def gui_otp_callback() -> str:
        st.session_state.otp_needed = True
        st.session_state.otp_value = None
        LOGGER.info("Browser needs OTP. Waiting for user input in GUI.")
        
        while st.session_state.otp_value is None:
            await asyncio.sleep(0.5)
            
        otp = st.session_state.otp_value
        st.session_state.otp_needed = False
        st.session_state.otp_value = None
        return otp

    try:
        browser = BrowserEngine(llm=llm, session_dir=settings.sessions_dir, headless=settings.browser_headless, otp_callback=gui_otp_callback)
        credential_manager = CredentialManager(store_path=settings.credentials_store)
        
        try:
            credentials = credential_manager.get_credentials(state["platform"])
        except KeyError:
            credentials = {}
            st.warning(f"No stored credentials for '{state['platform']}'. Proceeding with existing session or manual login.")

        platform = get_platform(state["platform"], browser)
        context = await browser.start(platform.name.lower().replace(" ", "_"))
        
        page = context.pages[0] if context.pages else await context.new_page()
        await platform.login(page, credentials)
        
        products = []
        if state.get("data_file_path"):
            products = load_product_data(Path(state["data_file_path"]), strict=False)
        elif state.get("sku") or state.get("product_name"):
            products = [{
                "sku": state.get("sku") or "UNSPECIFIED",
                "title": state.get("product_name")
            }]
        else:
            # Browse-to-find mode: use original command as the instruction
            user_command = state.get("command") or (st.session_state.messages[0]["content"] if st.session_state.messages else "Browse and find the product")
            products = [{
                "sku": "BROWSE",
                "title": user_command
            }]
            
        operation = state["operation"]
        for product in products:
            sku = product.get("sku", "UNSPECIFIED")
            image_paths = []
            if state.get("images_folder_path"):
                try:
                    image_paths = get_product_image_paths(Path(state["images_folder_path"]), sku)
                except:
                    pass
            
            if operation == "new_listing":
                await platform.create_listing(page, product, image_paths)
            elif operation in {"edit_listing", "bulk_update"}:
                updates = state.get("updates", {})
                if product.get("title") and "target_title" not in updates:
                    updates["target_title"] = product["title"]
                await platform.edit_listing(page, updates=updates, sku=sku)
        
        await browser.stop()
        
        # Cleanup uploaded files after execution
        if state.get("data_file_path"):
            path_to_clean = Path(state["data_file_path"])
            if path_to_clean.exists() and "temp_uploads" in str(path_to_clean):
                path_to_clean.unlink(missing_ok=True)
                st.session_state.listing_state["data_file_path"] = None
                
        return True, "Listing completed successfully!"
    except Exception as e:
        LOGGER.exception("GUI Execution failed")
        return False, str(e)

# Handle user input
if prompt := st.chat_input("What would you like to list today?"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    st.rerun() # Rerun to display user message and trigger assistant

# Logic for Assistant Response (triggered if the last message is from 'user')
if st.session_state.messages and st.session_state.messages[-1]["role"] == "user":
    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            history = [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages]
            state_result = asyncio.run(llm.analyze_conversation_state(history))
            # Keep the initial user command if not already set
            if not st.session_state.listing_state.get("command"):
                st.session_state.listing_state["command"] = history[0]["content"]
            st.session_state.listing_state.update(state_result)
            
            if state_result.get("is_complete"):
                user_cmd = st.session_state.listing_state.get("command") or history[0]["content"]
                workflow = asyncio.run(llm.interpret_user_command(user_cmd))
                st.session_state.listing_state["updates"] = workflow.get("updates", {})
                
                next_q = state_result.get("suggested_next_question") or ""
                is_fallback = "trouble reaching my brain" in next_q
                mode_str = " (Heuristic Mode 🧠)" if is_fallback else ""
                
                updates_text = "\n".join([f"  - `{k}`: `{v}`" for k, v in st.session_state.listing_state["updates"].items()])
                if not updates_text:
                    updates_text = "  - None detected"
                
                response = f"I've gathered everything! Here is the plan{mode_str}:\n\n" \
                           f"- **Platform**: {state_result['platform']}\n" \
                           f"- **Operation**: {state_result['operation']}\n" \
                           f"- **Target**: {state_result.get('sku') or state_result.get('product_name') or 'Batch file'}\n" \
                           f"- **Updates**:\n{updates_text}\n\n" \
                           f"Ready to execute?"
            else:
                response = state_result.get("suggested_next_question") or "Could you provide more details?"
                if "trouble reaching my brain" in response:
                    st.warning("⚠️ Running in limited brain mode (Quota or Connection issues). Basic tasks will still work.")
            
            st.markdown(response)
            st.session_state.messages.append({"role": "assistant", "content": response})
            st.rerun()

# Execution Logic (Only if state is complete and assistant has spoken)
current_state = st.session_state.listing_state
if current_state.get("is_complete") and not st.session_state.get("processing"):
    if st.button("🚀 Confirm and Execute Listing"):
        st.session_state.processing = True
        with st.status("Automating browser...") as status:
            success, msg = asyncio.run(run_listing_process(current_state))
            if success:
                status.update(label="Complete!", state="complete")
                st.success(msg)
                st.balloons()
            else:
                status.update(label="Failed", state="error")
                if "QUOTA_EXCEEDED" in msg:
                    st.error("⚠️ **Quota Exceeded!**")
                    st.info("Your Gemini Pro subscription needs to be linked to the API. Please enable 'Pay-as-you-go' in [Google AI Studio](https://aistudio.google.com/) to unlock higher limits.")
                else:
                    st.error(f"Execution Error: {msg}")
        st.session_state.processing = False
