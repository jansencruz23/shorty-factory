"""Per-mode composer implementations. The public dispatcher lives in
app.graph.composer; this package holds one module per mode."""

from app.graph.composers.narrative import compose_narrative
from app.graph.composers.top5 import compose_top5

__all__ = ["compose_narrative", "compose_top5"]
