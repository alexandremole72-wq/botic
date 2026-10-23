"""BOTic Bench: a realistic sandbox that tests whether an AI agent buys right
before it is allowed to pay.

The sandbox exposes trapped stores through the same tool names Shopify uses for
agents (UCP catalog and cart tools) and an ACP-style checkout. A deterministic
judge reads the final state of each run: what was ordered, from whom, for how
much, and to which bank account.

All stores, sellers and domains are fictional (.example domains). Payments use
test tokens only.
"""

__version__ = "0.1.0"
