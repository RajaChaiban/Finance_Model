"""Config loader for derivatives pricing pipeline."""

import yaml
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, Optional


@dataclass
class PricingConfig:
    """Validated pricing configuration."""
    # Option parameters
    option_type: str
    underlying: str
    spot_price: float
    strike_price: float
    days_to_expiration: int
    risk_free_rate: float
    volatility: float
    dividend_yield: float

    # Pricing engine parameters
    n_paths: int = 10000
    n_steps: int = 90
    variance_reduction: str = "none"

    # Optional barrier (for knockout options)
    barrier_level: Optional[float] = None
    barrier_type: Optional[str] = None

    # Output
    report_format: str = "html"
    save_to: str = "./reports/"

    def __post_init__(self):
        """Validate after initialization."""
        self._validate()

    def _validate(self):
        """Validate all parameters."""
        errors = []

        # Option type
        valid_types = ["american_put", "american_call", "european_put", "european_call", "knockout_call", "knockout_put"]
        if self.option_type not in valid_types:
            errors.append(f"option_type must be one of {valid_types}, got '{self.option_type}'")

        # Spot and strike
        if self.spot_price <= 0:
            errors.append(f"spot_price must be > 0, got {self.spot_price}")
        if self.strike_price <= 0:
            errors.append(f"strike_price must be > 0, got {self.strike_price}")

        # Time
        if self.days_to_expiration <= 0:
            errors.append(f"days_to_expiration must be > 0, got {self.days_to_expiration}")

        # Rates
        if self.risk_free_rate < 0:
            errors.append(f"risk_free_rate cannot be negative, got {self.risk_free_rate}")
        if self.dividend_yield < 0:
            errors.append(f"dividend_yield cannot be negative, got {self.dividend_yield}")

        # Volatility
        if self.volatility <= 0:
            errors.append(f"volatility must be > 0, got {self.volatility}")
        if self.volatility > 1.0:
            errors.append(f"volatility seems too high ({self.volatility:.0%}), expected < 100%")

        # Monte Carlo
        if self.n_paths <= 0:
            errors.append(f"n_paths must be > 0, got {self.n_paths}")
        if self.n_steps <= 0:
            errors.append(f"n_steps must be > 0, got {self.n_steps}")

        if self.variance_reduction not in ["none", "antithetic"]:
            errors.append(f"variance_reduction must be 'none' or 'antithetic', got '{self.variance_reduction}'")

        # Barrier (if knockout)
        if "knockout" in self.option_type:
            if self.barrier_level is None:
                errors.append("knockout options require barrier_level")
            if self.barrier_type not in ["down_and_out", "up_and_out"]:
                errors.append(f"barrier_type must be 'down_and_out' or 'up_and_out', got '{self.barrier_type}'")

        if errors:
            raise ValueError("Config validation failed:\n  " + "\n  ".join(errors))


def load_config(config_path: str) -> PricingConfig:
    """Load and validate config from YAML file.

    Args:
        config_path: Path to YAML config file

    Returns:
        PricingConfig with validated parameters

    Raises:
        FileNotFoundError: If config file doesn't exist
        yaml.YAMLError: If YAML is malformed
        ValueError: If validation fails
    """
    path = Path(config_path)

    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    try:
        with open(path, 'r') as f:
            raw_config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise yaml.YAMLError(f"Invalid YAML in {config_path}: {e}")

    if not raw_config:
        raise ValueError(f"Config file is empty: {config_path}")

    # Flatten nested config
    option_cfg = raw_config.get("option", {})
    pricing_cfg = raw_config.get("pricing", {})
    output_cfg = raw_config.get("output", {})

    # Build config dict
    config_dict = {
        # Option parameters
        "option_type": option_cfg.get("type"),
        "underlying": option_cfg.get("underlying"),
        "spot_price": option_cfg.get("spot_price"),
        "strike_price": option_cfg.get("strike_price"),
        "days_to_expiration": option_cfg.get("days_to_expiration"),
        "risk_free_rate": option_cfg.get("risk_free_rate"),
        "volatility": option_cfg.get("volatility"),
        "dividend_yield": option_cfg.get("dividend_yield", 0.0),

        # Pricing parameters
        "n_paths": pricing_cfg.get("n_paths", 10000),
        "n_steps": pricing_cfg.get("n_steps", 90),
        "variance_reduction": pricing_cfg.get("variance_reduction", "none"),

        # Barrier
        "barrier_level": option_cfg.get("barrier_level"),
        "barrier_type": option_cfg.get("barrier_type"),

        # Output
        "report_format": output_cfg.get("report_format", "html"),
        "save_to": output_cfg.get("save_to", "./reports/"),
    }

    # Validate required fields
    required = ["option_type", "underlying", "spot_price", "strike_price", "days_to_expiration", "risk_free_rate", "volatility"]
    missing = [k for k in required if config_dict.get(k) is None]
    if missing:
        raise ValueError(f"Config missing required fields: {missing}")

    return PricingConfig(**config_dict)
