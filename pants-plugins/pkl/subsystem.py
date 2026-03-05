"""PKL tool subsystem — manages downloading and running the pkl binary."""

from pants.core.util_rules.external_tool import ExternalTool
from pants.engine.platform import Platform
from pants.engine.rules import collect_rules


class PklTool(ExternalTool):
    """The PKL configuration language CLI (https://pkl-lang.org)."""

    options_scope = "pkl"
    name = "pkl"
    help = "The PKL configuration language CLI (https://pkl-lang.org)"

    default_version = "0.31.0"
    default_known_versions = [
        "0.31.0|macos_arm64|349402ae32c35382c034b0c0af744ffb0d53a213888c44deec94a7810e144889|98193008",
        "0.31.0|macos_x86_64|9f1cc8e3ac2327bc483b90d0c220da20eb785c3ba3fe92e021f47d3d56768282|100326344",
        "0.31.0|linux_x86_64|5a5c2a889b68ca92ff4258f9d277f92412b98dfef5057daef7564202a20870b6|100535568",
        "0.31.0|linux_arm64|471460cdd11e1cb9ac0a5401fdb05277ae3adb3a4573cc0a9c63ee087c1f93c8|97586680",
    ]

    # Maps Pants platform identifiers to the pkl release binary suffix.
    platform_mapping = {
        "macos_arm64": "macos-aarch64",
        "macos_x86_64": "macos-amd64",
        "linux_x86_64": "linux-amd64",
        "linux_arm64": "linux-aarch64",
    }

    def generate_url(self, plat: Platform) -> str:
        plat_str = self.platform_mapping[plat.value]
        return (
            f"https://github.com/apple/pkl/releases/download/"
            f"{self.version}/pkl-{plat_str}"
        )

    def generate_exe(self, plat: Platform) -> str:
        plat_str = self.platform_mapping[plat.value]
        return f"./pkl-{plat_str}"


def rules():
    return collect_rules()
