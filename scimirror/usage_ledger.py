"""Conservative, currency-separated Stage C budget accounting."""
import time
from dataclasses import dataclass, field


@dataclass
class UsageLedger:
    limits: dict
    logical_calls: int = 0
    http_attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    usage_unknown: int = 0
    costs: dict = field(default_factory=dict)
    started_at: float = field(default_factory=time.monotonic)

    @classmethod
    def from_export(cls, limits, saved):
        ledger=cls(limits)
        for name in ("logical_calls","http_attempts","input_tokens","output_tokens","usage_unknown"):
            setattr(ledger,name,int(saved.get(name,0)))
        ledger.costs={str(k):float(v) for k,v in saved.get("costs_by_currency",{}).items()}
        ledger.started_at=time.monotonic()-float(saved.get("elapsed_seconds",0.0))
        return ledger

    def reserve(self, input_tokens, output_tokens, currency=None, cost=0.0, http=False):
        if time.monotonic()-self.started_at > self.limits.get("walltime_seconds",float("inf")):
            raise RuntimeError("Stage C walltime budget exhausted")
        logical = self.logical_calls + (0 if http else 1)
        attempts = self.http_attempts + (1 if http else 0)
        if logical > self.limits["max_logical_calls"] or attempts > self.limits["max_http_attempts"]:
            raise RuntimeError("Stage C call budget exhausted")
        if self.input_tokens + input_tokens > self.limits["max_input_tokens"]:
            raise RuntimeError("Stage C input-token budget exhausted")
        if self.output_tokens + output_tokens > self.limits["max_output_tokens"]:
            raise RuntimeError("Stage C output-token budget exhausted")
        if currency:
            next_cost = self.costs.get(currency, 0.0) + cost
            maximum = self.limits["max_total_cost_by_currency"].get(currency)
            if maximum is None or next_cost > maximum:
                raise RuntimeError("Stage C monetary budget exhausted")
            self.costs[currency] = next_cost
        self.logical_calls = logical
        self.http_attempts = attempts
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens

    def record_unknown(self):
        self.usage_unknown += 1

    def export(self):
        return {"logical_calls": self.logical_calls, "http_attempts": self.http_attempts,
                "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "usage_unknown": self.usage_unknown, "costs_by_currency": self.costs,
                "elapsed_seconds": time.monotonic()-self.started_at}
