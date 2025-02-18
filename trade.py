import os
import asyncio
import logging
from web3 import Web3
from dotenv import load_dotenv
from cachetools import cached, TTLCache
import aiohttp
from tenacity import retry, stop_after_attempt, wait_fixed

# Load environment variables securely
load_dotenv()

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Configuration loading
INFURA_KEY = os.getenv("INFURA_KEY")
ETHERSCAN_KEY = os.getenv("ETHERSCAN_KEY")

# Web3 initialization with connection check
web3 = Web3(Web3.HTTPProvider(f"https://mainnet.infura.io/v3/{INFURA_KEY}"))
if not web3.isConnected():
    logger.critical("Failed to connect to Ethereum node. Exiting...")
    raise ConnectionError("Failed to connect to Ethereum node")

# Constants
UNISWAP_FACTORY_ADDRESS = '0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f'
TOKEN_ABI = [...]  # ERC-20 ABI (replace with actual ABI)
UNISWAP_FACTORY_ABI = [...]  # Uniswap Factory ABI (replace with actual ABI)

# Caching setup
cache = TTLCache(maxsize=100, ttl=300)  # Cache expires after 5 minutes

# Utility functions
def validate_address(address: str) -> bool:
    """Validate Ethereum address."""
    if not Web3.isAddress(address):
        logger.warning(f"Invalid Ethereum address: {address}")
        return False
    if not Web3.isChecksumAddress(address):
        logger.warning(f"Address is not checksummed: {address}")
        return False
    return True

@cached(cache)
def get_token_owner(token_address: str) -> str:
    """Get the owner of a token contract."""
    if not validate_address(token_address):
        raise ValueError(f"Invalid token address: {token_address}")
    token_contract = web3.eth.contract(address=token_address, abi=TOKEN_ABI)
    try:
        return token_contract.functions.owner().call()
    except Exception as e:
        logger.error(f"Error fetching owner for token {token_address}: {e}")
        return None

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
async def async_get(url: str, headers=None):
    """Asynchronous HTTP GET request."""
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            response.raise_for_status()
            return await response.json()

async def get_token_price(token_address: str) -> float:
    """Fetch token price using an oracle or API."""
    if not validate_address(token_address):
        raise ValueError(f"Invalid token address: {token_address}")
    url = f"https://api.coingecko.com/api/v3/simple/token_price/ethereum?contract_addresses={token_address}&vs_currencies=eth"
    try:
        data = await async_get(url)
        return data[token_address.lower()]['eth']
    except Exception as e:
        logger.error(f"Error fetching price for token {token_address}: {e}")
        return None

def get_new_token_pairs(from_block: int, to_block: int):
    """Fetch new token pairs created in a block range."""
    factory = web3.eth.contract(address=UNISWAP_FACTORY_ADDRESS, abi=UNISWAP_FACTORY_ABI)
    try:
        events = factory.events.PairCreated.getLogs(fromBlock=from_block, toBlock=to_block)
        return events
    except Exception as e:
        logger.error(f"Error fetching new token pairs: {e}")
        return []

# Persistent storage for last processed block
def load_last_processed_block() -> int:
    """Load the last processed block from persistent storage."""
    try:
        with open("last_block.txt", "r") as f:
            return int(f.read().strip())
    except FileNotFoundError:
        return 0

def save_last_processed_block(block_number: int):
    """Save the last processed block to persistent storage."""
    with open("last_block.txt", "w") as f:
        f.write(str(block_number))

# Monitoring loop
async def monitor_tokens():
    last_processed_block = load_last_processed_block()
    while True:
        try:
            latest_block = web3.eth.block_number
            if latest_block > last_processed_block:
                logger.info(f"Processing blocks {last_processed_block + 1} to {latest_block}")
                new_pairs = get_new_token_pairs(last_processed_block + 1, latest_block)
                await process_pairs(new_pairs)
                last_processed_block = latest_block
                save_last_processed_block(last_processed_block)
            await asyncio.sleep(15)  # Check every 15 seconds
        except Exception as e:
            logger.error(f"Error in monitoring loop: {e}")
            await asyncio.sleep(60)  # Wait before retrying

async def process_pairs(pairs):
    """Process new token pairs."""
    for pair in pairs:
        token0 = pair['args']['token0'].lower()
        token1 = pair['args']['token1'].lower()
        pair_address = pair['args']['pair']

        # Validate addresses
        if not validate_address(token0) or not validate_address(token1):
            logger.warning(f"Invalid token address detected: {token0} or {token1}")
            continue

        # Blacklist checks
        if is_token_blacklisted(token0) or is_token_blacklisted(token1):
            logger.info(f"Token {token0} or {token1} is blacklisted. Skipping...")
            continue

        dev_address = get_token_owner(token0)
        if dev_address and is_dev_blacklisted(dev_address):
            logger.info(f"Developer of token {token0} is blacklisted. Skipping...")
            continue

        # Volume and quality checks
        if has_fake_volume(token0):
            logger.info(f"Token {token0} has fake volume. Adding to blacklist...")
            coin_blacklist.add(token0)
            continue

        if not is_token_good(token0):
            logger.info(f"Token {token0} is not marked as 'Good' by RugCheck. Skipping...")
            continue

        # Liquidity and price filters
        if not meets_liquidity_filter(pair_address):
            logger.info(f"Pair {pair_address} does not meet liquidity requirements. Skipping...")
            continue

        current_price = await get_token_price(token0)
        previous_price = load_previous_price(token0)  # Implement persistent storage for prices
        if not meets_price_change_filter(token0, previous_price, current_price):
            logger.info(f"Token {token0} has exceeded max price change. Skipping...")
            continue

        # Holder count filter
        if not meets_holder_count_filter(token0):
            logger.info(f"Token {token0} does not meet minimum holder count. Skipping...")
            continue

        # Bundle purchase detection
        transaction = get_transaction_details(pair_address)
        if is_bundle_purchase(transaction):
            logger.info(f"Transaction for token {token0} is a bundle purchase. Skipping...")
            continue

        # Proceed with trade
        logger.info(f"New pair created: {pair['args']}")
        alert_message = (
            f"New token detected: {token0}\n"
            f"Price: {current_price} ETH\n"
            f"Liquidity: {get_liquidity(pair_address)}"
        )
        send_telegram_alert(alert_message)
        execute_trade(token_address=token0, action="buy", amount=0.1)

# Helper functions (implement these based on your requirements)
def is_token_blacklisted(token_address: str) -> bool:
    """Check if a token is blacklisted."""
    return token_address in coin_blacklist

def is_dev_blacklisted(dev_address: str) -> bool:
    """Check if a developer is blacklisted."""
    return dev_address in dev_blacklist

def has_fake_volume(token_address: str) -> bool:
    """Check if a token has fake volume."""
    # Use Pocket Universe or similar service
    return False

def is_token_good(token_address: str) -> bool:
    """Check if a token is marked as 'Good' by RugCheck."""
    return True

def meets_liquidity_filter(pair_address: str) -> bool:
    """Check if a pair meets liquidity requirements."""
    return True

def meets_price_change_filter(token_address: str, previous_price: float, current_price: float) -> bool:
    """Check if price change is within acceptable limits."""
    return True

def meets_holder_count_filter(token_address: str) -> bool:
    """Check if a token meets minimum holder count."""
    return True

def is_bundle_purchase(transaction) -> bool:
    """Detect bundle purchases."""
    return False

def get_transaction_details(pair_address: str):
    """Fetch transaction details."""
    return {}

def get_liquidity(pair_address: str) -> float:
    """Fetch liquidity for a pair."""
    return 0.0

def send_telegram_alert(message: str):
    """Send a Telegram alert."""
    pass

def execute_trade(token_address: str, action: str, amount: float):
    """Execute a trade."""
    pass

def load_previous_price(token_address: str) -> float:
    """Load the previous price of a token from persistent storage."""
    try:
        with open(f"prices/{token_address}.txt", "r") as f:
            return float(f.read().strip())
    except FileNotFoundError:
        return 0.0

def save_previous_price(token_address: str, price: float):
    """Save the previous price of a token to persistent storage."""
    os.makedirs("prices", exist_ok=True)
    with open(f"prices/{token_address}.txt", "w") as f:
        f.write(str(price))

# Entry point
if __name__ == "__main__":
    asyncio.run(monitor_tokens())