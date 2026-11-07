"""Per-device entity names. State translations still follow the HA profile."""
import json
from pathlib import Path

NAME_LANGUAGES = ("system", "pl", "en")


def load_name_catalogs():
    root = Path(__file__).parent / "translations"
    return {lang: {domain: {key: entry["name"] for key, entry in entities.items()}
                   for domain, entities in json.loads((root / f"{lang}.json").read_text(encoding="utf-8"))["entity"].items()}
            for lang in ("pl", "en")}


class AuxNames:
    """HA retains registry user names and entity IDs when original names change."""
    _aux_name_domain = None

    @property
    def name(self):
        translated = self.coordinator.entity_name(self._aux_name_domain, self._attr_translation_key)
        if translated is not None:
            return translated
        return super().name
