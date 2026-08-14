"""Load the multilingual dish lexicon into ``ref.food_name``.

The lexicon YAML is the authority for dish IDENTITY — canonical English name, native
script names, romanised aliases, and which member of a family is the unmodified one.
The template library is the authority for COMPUTATION. They are separate files with
separate reviewers (a linguist and a dietitian), and they join on the dish key.

Before this existed, the template loader derived dish names by title-casing the key,
so ``dosa_plain`` became "Dosa Plain" while the lexicon said "Dosa, plain" — two rows
for one dish, and the resolver could find neither reliably. Loading both from the same
key is what keeps them in step.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["DishEntry", "LexiconLoadResult", "load_lexicon_file", "load_lexicon_into_postgres"]

_LANGS = ("ta", "te", "ml", "kn")


@dataclass(frozen=True)
class DishEntry:
    key: str
    en: str
    group: str
    base: bool
    regions: tuple[str, ...]
    native: dict[str, str]
    aliases: tuple[str, ...]


@dataclass
class LexiconLoadResult:
    dishes_seen: int = 0
    dishes_created: int = 0
    names_written: int = 0
    skipped: list[str] = None            # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.skipped is None:
            self.skipped = []

    def as_dict(self) -> dict[str, Any]:
        return {"dishes_seen": self.dishes_seen, "dishes_created": self.dishes_created,
                "names_written": self.names_written, "skipped": self.skipped}


def load_lexicon_file(path: str | Path) -> dict[str, DishEntry]:
    import yaml

    with open(path, encoding="utf-8") as fh:
        payload = yaml.safe_load(fh) or {}
    out: dict[str, DishEntry] = {}
    for row in payload.get("dishes", ()):
        key = row["key"]
        out[key] = DishEntry(
            key=key, en=row["en"], group=row.get("group", "prepared_dish"),
            base=bool(row.get("base", False)),
            regions=tuple(row.get("regions", ())),
            native={lang: row[lang] for lang in _LANGS if row.get(lang)},
            aliases=tuple(row.get("aliases", ())),
        )
    return out


def load_lexicon_into_postgres(
    dishes: dict[str, DishEntry],
    conn,
    *,
    source_key: str = "PATHYAM-LEXICON",
) -> LexiconLoadResult:
    """Upsert dish rows and every name variant. Idempotent."""
    result = LexiconLoadResult()

    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO ref.source (source_key, citation, licence, is_commercial_cleared, notes)
               VALUES (%s, %s, %s, true, %s)
               ON CONFLICT (source_key) DO UPDATE SET notes = EXCLUDED.notes
               RETURNING source_id""",
            (source_key, "Pathyam South Indian dish lexicon (draft).", "Proprietary",
             "Native-script forms need native-speaker review; see the file header."),
        )
        source_id = cur.fetchone()[0]

        cur.execute(
            """SELECT coalesce(max(substring(pathyam_id from 6)::int), 0)
                 FROM ref.food_item WHERE pathyam_id ~ '^PY-F-[0-9]{6}$'"""
        )
        next_id = int(cur.fetchone()[0]) + 1

        for entry in dishes.values():
            result.dishes_seen += 1

            cur.execute(
                "SELECT food_id FROM ref.food_item WHERE canonical_name_en = %s",
                (entry.en,),
            )
            row = cur.fetchone()
            if row:
                food_id = row[0]
                cur.execute(
                    """UPDATE ref.food_item
                          SET is_recipe = true, food_group = %s, updated_at = now()
                        WHERE food_id = %s""",
                    (entry.group, food_id),
                )
            else:
                pathyam_id = f"PY-F-{next_id:06d}"
                next_id += 1
                cur.execute(
                    """INSERT INTO ref.food_item
                         (pathyam_id, canonical_name_en, food_group, is_recipe)
                       VALUES (%s, %s, %s, true) RETURNING food_id""",
                    (pathyam_id, entry.en, entry.group),
                )
                food_id = cur.fetchone()[0]
                result.dishes_created += 1

            # Replace names wholesale: the YAML is the source of truth, and a partial
            # upsert would leave stale aliases from a previous edit.
            cur.execute("DELETE FROM ref.food_name WHERE food_id = %s", (food_id,))

            rows: list[tuple[str, str | None, str | None, bool]] = [
                ("en", None, entry.en, True)
            ]
            for lang, native in entry.native.items():
                rows.append((lang, native, None, True))
            for alias in entry.aliases:
                if alias.strip().lower() == entry.en.strip().lower():
                    continue
                rows.append(("en", None, alias, False))

            seen: set[tuple[str, str]] = set()
            for lang, native, roman, is_primary in rows:
                surface = (native or roman or "").strip()
                if not surface:
                    continue
                # ref.food_name is unique on (food_id, lang, normalized), and two
                # aliases can normalise to the same key. Skip rather than error.
                dedupe_key = (lang, surface.lower())
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                try:
                    cur.execute(
                        """INSERT INTO ref.food_name
                             (food_id, lang, name_native, name_roman, is_primary, source_id)
                           VALUES (%s, %s, %s, %s, %s, %s)
                           ON CONFLICT DO NOTHING""",
                        (food_id, lang, native, roman, is_primary, source_id),
                    )
                    result.names_written += cur.rowcount
                except Exception as exc:                        # noqa: BLE001
                    result.skipped.append(f"{entry.key}/{lang}/{surface}: {exc}")

        conn.commit()
    return result
