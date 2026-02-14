from platforms.common import LLMDrivenPlatform


class ShopifyPlatform(LLMDrivenPlatform):
    name = "Shopify Admin"
    login_url = "https://admin.shopify.com"
