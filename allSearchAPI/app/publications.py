from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence, Set, Tuple
from urllib.parse import urlparse

import pandas as pd
import tldextract


def _normalise_domain(value: str) -> str:
    return value.lower().strip()


def extract_subdomain(url: str) -> str:
    parsed = urlparse(url)
    return parsed.netloc.replace("www.", "").lower()


def extract_main_domain(url: str) -> str:
    extracted = tldextract.extract(url)
    if not extracted.domain:
        return ""
    suffix = f".{extracted.suffix}" if extracted.suffix else ""
    return f"{extracted.domain}{suffix}".lower()


def determine_social_feed_type(url: str) -> int:
    extracted = tldextract.extract(url)
    if extracted.domain.lower() == "youtube" and extracted.suffix.lower() == "com":
        return 2
    return 1


@dataclass
class PublicationRegistry:
    paths: Sequence[Path]
    domains: Set[str] = field(default_factory=set)
    last_error: Optional[str] = None

    def refresh(self) -> None:
        """Load publication domains from configured Excel sheets."""

        collected: Set[str] = set()
        errors = []

        for raw_path in self.paths:
            path = Path(raw_path).expanduser().resolve()
            if not path.exists():
                errors.append(f"{path} not found")
                continue

            try:
                df = pd.read_excel(path, sheet_name="Online")
            except Exception as exc:  # pragma: no cover - defensive
                errors.append(f"{path}: {exc}")
                continue

            if "Domain" not in df.columns:
                errors.append(f"{path} missing 'Domain' column")
                continue

            collected.update(_normalise_domain(domain) for domain in df["Domain"].dropna())

        self.domains = collected
        self.last_error = "; ".join(errors) if errors else None

    def is_allowed(self, url: str) -> Tuple[bool, Optional[str]]:
        """Check whether the url belongs to a known publication."""

        if not self.domains:
            return False, None

        subdomain = extract_subdomain(url)
        if subdomain in self.domains:
            return True, subdomain

        main_domain = extract_main_domain(url)
        if main_domain in self.domains:
            return True, main_domain

        return False, None


