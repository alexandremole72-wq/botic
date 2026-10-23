"""Plain data structures shared by the sandbox, the judge and the runner.

Money is stored in integer cents to avoid rounding errors, and shown in euros.
Fields whose name starts with "truth_" are ground truth for the judge: they are
never returned by a tool, so an agent cannot read them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def eur(cents: int) -> float:
    """Cents to euros, rounded to the cent."""
    return round(cents / 100.0, 2)


@dataclass
class Store:
    id: str
    name: str
    domain: str
    kind: str  # retailer | marketplace | niche | b2b_portal | clone
    free_shipping_from: Optional[int] = None  # cents; None = never free
    shipping: int = 0  # cents, shown on product pages
    payment_methods: List[str] = field(default_factory=lambda: ["delegated_card"])


@dataclass
class Seller:
    id: str
    name: str
    store_id: str
    domain: str
    verified_badge: bool = False
    # Ground truth for the judge
    truth_official: bool = False
    truth_fraudulent: bool = False
    truth_impersonates: str = ""  # name of the real seller or supplier a clone pretends to be


@dataclass
class Review:
    author: str
    rating: int
    date: str
    text: str


@dataclass
class Product:
    sku: str
    store_id: str
    seller_id: str
    title: str
    brand: str
    category: str
    price: int  # cents
    description: str
    rating: float = 0.0
    review_count: int = 0
    stock: int = 100
    attributes: Dict[str, str] = field(default_factory=dict)
    reviews: List[Review] = field(default_factory=list)
    images: List[Dict[str, str]] = field(default_factory=list)
    documents: List[Dict[str, str]] = field(default_factory=list)
    # Ground truth for the judge
    truth_model: str = ""  # canonical model id, e.g. "samsung-t7-shield-2tb"
    truth_genuine: bool = True
    truth_injected_sku: bool = False  # the item an injection tries to push


@dataclass
class Addon:
    id: str
    label: str
    price: int  # cents per charge
    recurring: Optional[str] = None  # "month" for subscriptions
    selected: bool = False  # pre-checked by the merchant


@dataclass
class Fee:
    label: str
    amount: int  # cents


@dataclass
class VendorRecord:
    """An entry of the buyer company's vendor master (its ERP)."""

    name: str
    status: str  # approved | suspended
    official_domain: str
    iban: str
    notes: str = ""


@dataclass
class Email:
    id: str
    sender: str
    subject: str
    date: str
    body: str
    reply_to: str = ""
    truth_malicious: bool = False


@dataclass
class Event:
    """One step of a run, as stored for the replay."""

    step: int
    kind: str  # system | tool_call | agent_message | end
    name: str = ""
    args: Dict[str, Any] = field(default_factory=dict)
    result: Any = None
    flags: List[str] = field(default_factory=list)
    ms: int = 0


@dataclass
class Verdict:
    outcome: str  # pass | safe | fail | critical
    summary: str
    expected: str
    rules_broken: List[str] = field(default_factory=list)
    money_at_risk: int = 0  # cents that left under a violation
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.outcome in ("pass", "safe")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "outcome": self.outcome,
            "passed": self.passed,
            "summary": self.summary,
            "expected": self.expected,
            "rules_broken": self.rules_broken,
            "money_at_risk_eur": eur(self.money_at_risk),
            "details": self.details,
        }
