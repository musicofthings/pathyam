"""Meal journal persistence.

Replaces the module-level ``_JOURNAL_STORE`` list that previously held every user's
history in process memory -- lost on restart, and shared between everyone who could
reach the API.

Rows live in ``app.meal_log`` and ``app.meal_log_item`` (db/007, db/013). Deletes are
soft: ``deleted_at`` is stamped rather than the row removed, so a mis-log can be
undone and so deleting never silently rewrites someone's dietary history.

IDENTITY
--------
There is no authentication yet. ``DEV_USER_ID`` is a fixed pseudonymous row that all
unattributed logs land on, so the ``user_id`` column and the per-user query paths are
real and exercised, while the thing that decides *which* user you are is not built.
Anyone who can reach the API is that user. Wiring real auth is Phase 6; when it
lands, only :func:`resolve_user_id` should need to change.
"""

from __future__ import annotations

import datetime as _dt
import json
import uuid
from typing import Any

from . import schemas as s

__all__ = ["DEV_USER_ID", "resolve_user_id", "dev_identity_allowed",
           "JournalRepository", "GlucoseRepository"]

# Matches the row seeded by db/013_meal_log_app_fields.sql.
DEV_USER_ID = "00000000-0000-0000-0000-000000000001"

# app.meal_log.meal_slot carries the coarse clinical grouping; the app's finer
# meal_type is stored alongside it. This is the projection between them.
_SLOT_FOR_MEAL_TYPE = {
    "breakfast": "breakfast",
    "morning_snack": "snack",
    "tiffin": "tiffin",
    "lunch": "lunch",
    "afternoon_snack": "snack",
    "evening_snack": "snack",
    "dinner": "dinner",
    "late_night_snack": "snack",
    "other": "other",
}


# Set PATHYAM_ALLOW_DEV_IDENTITY=1 to keep accepting the old unverified
# X-Pathyam-User header. It exists so the existing test suite and local development
# keep working; it is refused when PATHYAM_ENV=production, because a self-asserted
# identity header alongside real authentication is a way in, not a convenience.
_DEV_IDENTITY_ENV = "PATHYAM_ALLOW_DEV_IDENTITY"


def dev_identity_allowed() -> bool:
    import os

    if os.environ.get("PATHYAM_ENV", "development") == "production":
        return False
    return os.environ.get(_DEV_IDENTITY_ENV, "1").strip() not in ("", "0", "false")


def resolve_user_id(header_value: str | None) -> str:
    """Resolve an UNVERIFIED identity header. Development only.

    Superseded by real authentication (``pathyam_api.auth``). This is retained for
    local development and for the tests written before auth existed, is refused in
    production, and must never be treated as a credential: the header is
    self-asserted and proves nothing.
    """
    if header_value and dev_identity_allowed():
        try:
            return str(uuid.UUID(header_value))
        except (ValueError, AttributeError):
            pass
    return DEV_USER_ID


def _iso(value: _dt.datetime | None) -> str:
    if value is None:
        return _dt.datetime.now().isoformat(timespec="seconds")
    return value.isoformat(timespec="seconds")


class JournalRepository:
    """Reads and writes the meal journal. One instance per request connection."""

    def __init__(self, conn) -> None:
        self._conn = conn

    # ------------------------------------------------------------- writing --

    def insert_entry(
        self,
        *,
        user_id: str,
        entry_id: str | None,
        consumed_at: str,
        meal_type: str,
        query_text: str,
        items: list[s.LogItemOut],
        region_id: int | None = None,
        method: str = "text",
    ) -> str:
        """Persist one logged meal and its computed items. Returns the meal_log_id."""
        meal_log_id = entry_id or str(uuid.uuid4())

        with self._conn.cursor() as cur:
            # app.meal_log.user_id is NOT NULL and references app.app_user, so the
            # row has to exist. Until there is auth, a user is created on first
            # write. app.app_user is pseudonymous by design -- no name, email or
            # phone -- so this stores an identifier and nothing else about anyone.
            cur.execute(
                """INSERT INTO app.app_user (user_id, preferred_lang, is_anonymised)
                   VALUES (%s, 'en', true)
                   ON CONFLICT (user_id) DO NOTHING""",
                (user_id,),
            )
            cur.execute(
                """INSERT INTO app.meal_log
                       (meal_log_id, user_id, consumed_at, meal_slot, meal_type,
                        method, region_id, notes)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING meal_log_id""",
                (
                    meal_log_id, user_id, consumed_at,
                    _SLOT_FOR_MEAL_TYPE.get(meal_type, "other"), meal_type,
                    method, region_id, query_text,
                ),
            )

            for item in items:
                computed = item.computed
                if computed is None:
                    # Nothing resolved, so there is no template or food to attach an
                    # item row to -- meal_log_item_target_ck requires one, correctly.
                    # The line is not lost: the user's original text is kept verbatim
                    # in meal_log.notes, so the meal can be re-resolved later. It is
                    # excluded from the totals, which is the honest reading: an
                    # uncomputed item is unknown, not zero.
                    continue

                # meal_log_item stores a PER-SERVING figure alongside a serving
                # count, and readers multiply the two. The engine's per_serving
                # values are already scaled by `portions`, so they must be divided
                # back out before storage -- otherwise portions are applied twice
                # and a 2-idli log reads as 4.
                servings = float(computed.portions or 1.0)
                scale = 1.0 / servings if servings > 0 else 1.0

                nutrients = {
                    tag: {
                        "p10": n.per_serving.p10 * scale,
                        "p50": n.per_serving.p50 * scale,
                        "p90": n.per_serving.p90 * scale,
                        "unit": n.unit,
                    }
                    for tag, n in computed.nutrients.items()
                }
                energy = computed.nutrients.get("ENERC_KCAL")

                cur.execute(
                    """INSERT INTO app.meal_log_item
                           (meal_log_id, template_id, param_bindings, servings,
                            energy_kcal_p50, energy_kcal_p10, energy_kcal_p90,
                            nutrients, dominant_uncertainty_param, engine_version,
                            computed_at)
                       SELECT %s, t.template_id, %s, %s, %s, %s, %s, %s, %s, %s, now()
                         FROM ref.recipe_template t
                        WHERE t.pathyam_id = %s""",
                    (
                        meal_log_id,
                        json.dumps(computed.parameter_summary or {}),
                        servings,
                        energy.per_serving.p50 * scale if energy else None,
                        energy.per_serving.p10 * scale if energy else None,
                        energy.per_serving.p90 * scale if energy else None,
                        json.dumps(nutrients),
                        computed.dominant_uncertainty_param,
                        computed.engine_version,
                        computed.template,
                    ),
                )

        self._conn.commit()
        return meal_log_id

    # ------------------------------------------------------------- reading --

    _SELECT = """
        SELECT m.meal_log_id, m.consumed_at, m.meal_type, m.notes,
               coalesce(sum(i.energy_kcal_p50 * i.servings), 0)              AS kcal,
               coalesce(sum((i.nutrients->'PROCNT'->>'p50')::numeric
                            * i.servings), 0)                                AS protein,
               coalesce(sum((i.nutrients->'FAT'->>'p50')::numeric
                            * i.servings), 0)                                AS fat,
               coalesce(sum((i.nutrients->'CHOAVLDF'->>'p50')::numeric
                            * i.servings), 0)                                AS carbs,
               coalesce(sum((i.nutrients->'FIBTG'->>'p50')::numeric
                            * i.servings), 0)                                AS fibre,
               coalesce(sum(i.servings), 1)                                  AS portions
          FROM app.meal_log m
          LEFT JOIN app.meal_log_item i USING (meal_log_id)
         WHERE m.user_id = %s AND m.deleted_at IS NULL
    """

    def list_entries(self, user_id: str, date: str | None = None) -> list[s.JournalEntry]:
        """Entries for one date (YYYY-MM-DD), newest first. No date = all of them."""
        sql = self._SELECT
        params: list[Any] = [user_id]
        if date:
            sql += " AND m.consumed_at::date = %s::date"
            params.append(date)
        sql += """ GROUP BY m.meal_log_id, m.consumed_at, m.meal_type, m.notes
                   ORDER BY m.consumed_at DESC"""

        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

        return [self._to_entry(row) for row in rows]

    def get_entry(self, user_id: str, entry_id: str) -> s.JournalEntry | None:
        sql = self._SELECT + """ AND m.meal_log_id = %s
                   GROUP BY m.meal_log_id, m.consumed_at, m.meal_type, m.notes"""
        with self._conn.cursor() as cur:
            cur.execute(sql, (user_id, entry_id))
            row = cur.fetchone()
        return self._to_entry(row) if row else None

    @staticmethod
    def _to_entry(row) -> s.JournalEntry:
        (log_id, consumed_at, meal_type, notes,
         kcal, protein, fat, carbs, fibre, _portions) = row

        carbs_f = float(carbs)
        fibre_f = float(fibre)
        fat_f = float(fat)
        return s.JournalEntry(
            id=str(log_id),
            consumed_at=_iso(consumed_at),
            meal_type=meal_type or "other",
            query_text=notes or "",
            total_kcal=round(float(kcal), 1),
            protein_g=round(float(protein), 1),
            fat_g=round(fat_f, 1),
            carbs_g=round(carbs_f, 1),
            fibre_g=round(fibre_f, 1),
            peak_glucose_mg_dl=round(_illustrative_peak(carbs_f, fat_f, fibre_f), 1),
            items=[],
        )

    # ------------------------------------------------------------ mutating --

    def soft_delete(self, user_id: str, entry_id: str) -> bool:
        with self._conn.cursor() as cur:
            cur.execute(
                """UPDATE app.meal_log SET deleted_at = now()
                    WHERE meal_log_id = %s AND user_id = %s AND deleted_at IS NULL""",
                (entry_id, user_id),
            )
            changed = cur.rowcount
        self._conn.commit()
        return changed > 0

    def adjust_portions(self, user_id: str, entry_id: str, delta: float) -> bool:
        """Shift every item's servings by ``delta``, floored at half a portion.

        Returns whether the ENTRY exists and belongs to this user -- not whether any
        row changed. A meal none of whose lines resolved has no items to scale, and
        reporting that as "not found" would be wrong: the entry is right there in
        the user's history.
        """
        with self._conn.cursor() as cur:
            cur.execute(
                """SELECT 1 FROM app.meal_log
                    WHERE meal_log_id = %s AND user_id = %s AND deleted_at IS NULL""",
                (entry_id, user_id),
            )
            if cur.fetchone() is None:
                return False

            cur.execute(
                """UPDATE app.meal_log_item i
                      SET servings = greatest(0.5, i.servings + %s)
                     FROM app.meal_log m
                    WHERE i.meal_log_id = m.meal_log_id
                      AND m.meal_log_id = %s
                      AND m.user_id = %s
                      AND m.deleted_at IS NULL""",
                (delta, entry_id, user_id),
            )
        self._conn.commit()
        return True


# Assumed GI when a logged meal has no measured value. IFCT does not publish GI for
# most foods, so this is a placeholder in the middle of the range for Indian mixed
# meals -- another reason the journal's peak figure is illustrative only.
_ASSUMED_GI = 68.0
_ASSUMED_BASELINE_MG_DL = 95.0


class GlucoseRepository:
    """Reads and writes continuous glucose readings (db/014)."""

    def __init__(self, conn) -> None:
        self._conn = conn

    def ingest(self, user_id: str, readings: list[Any]) -> tuple[int, int]:
        """Store a batch. Returns (accepted, duplicates_updated).

        Idempotent on (user_id, reading_at): sensors resend on reconnect and clients
        retry, and a doubled reading would bias anything fitted from this table.
        """
        if not readings:
            return (0, 0)

        with self._conn.cursor() as cur:
            cur.execute(
                """INSERT INTO app.app_user (user_id, preferred_lang, is_anonymised)
                   VALUES (%s, 'en', true) ON CONFLICT (user_id) DO NOTHING""",
                (user_id,),
            )

            accepted = duplicates = 0
            for reading in readings:
                cur.execute(
                    """INSERT INTO app.cgm_reading
                           (user_id, reading_at, glucose_mg_dl, trend_arrow)
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT (user_id, reading_at) DO UPDATE
                           SET glucose_mg_dl = EXCLUDED.glucose_mg_dl,
                               trend_arrow = EXCLUDED.trend_arrow,
                               ingested_at = now()
                       RETURNING (xmax = 0) AS inserted""",
                    (user_id, reading.timestamp, reading.glucose_mg_dl,
                     reading.trend_arrow),
                )
                row = cur.fetchone()
                if row and row[0]:
                    accepted += 1
                else:
                    duplicates += 1

        self._conn.commit()
        return (accepted, duplicates)

    def count_for_user(self, user_id: str) -> int:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM app.cgm_reading WHERE user_id = %s", (user_id,)
            )
            return int(cur.fetchone()[0])

    def postprandial_readings(self, user_id: str, meal_log_id: str) -> list[dict[str, Any]]:
        """Readings in the 3h after one meal, as minutes since it.

        This is the shape a fitted CGT model would be scored against. Nothing
        currently fits against it -- the shipped curve is illustrative.
        """
        with self._conn.cursor() as cur:
            cur.execute(
                """SELECT minutes_since_meal, glucose_mg_dl
                     FROM app.v_postprandial_reading
                    WHERE user_id = %s AND meal_log_id = %s
                    ORDER BY reading_at""",
                (user_id, meal_log_id),
            )
            return [
                {"minutes_since_meal": round(float(m), 1),
                 "glucose_mg_dl": float(g)}
                for m, g in cur.fetchall()
            ]


def _illustrative_peak(carbs_g: float, fat_g: float, fibre_g: float) -> float:
    """Rough postprandial peak for the journal row. Display only.

    Delegates to pathyam_engine.cgt so the journal and the CGT tab cannot drift
    apart -- this previously carried its own copy of the same four coefficients.
    Not a validated model; see that module's docstring.
    """
    from pathyam_engine import cgt

    return cgt.predict_curve(
        carbs_g=carbs_g, gi=_ASSUMED_GI, fibre_g=fibre_g, fat_g=fat_g,
        baseline_mg_dl=_ASSUMED_BASELINE_MG_DL,
    ).peak_mg_dl
