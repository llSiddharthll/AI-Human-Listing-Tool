from platforms.common import LLMDrivenPlatform


class AmazonPlatform(LLMDrivenPlatform):
    name = "Amazon Seller Central"
    login_url = "https://sellercentral.amazon.com"
