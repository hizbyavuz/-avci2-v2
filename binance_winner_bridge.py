#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Binance Avci 2 ile Winner Anatomy arasindaki gozlemsel kopru.

Bu dosya:
- Yalnizca 72 saati tamamlanmis CLOSED winner olaylarini kullanir.
- Winner olaylarini eslesmis basarisiz kontrollerle karsilastirir.
- Son Avci 2 adaylarini bu iki tarihsel gruba benzetir.
- Avci 2 kurallarini veya esiklerini degistirmez.
- Sonuclari winner_bridge_scores tablosuna kaydeder.
"""

import json
import math
import sqlite3
import statistics
from datetime import datetime, timezone


DB_FILE = "binance_avci2.db"
BRIDGE_VERSION = "winner-bridge-v1.0-observational"

OFFSETS_HOURS = (
    72,
    48,
    24,
    12,
    6,
    3,
    1,
)

MIN_CLASS_SAMPLES = 5
MAX_STANDARD_DISTANCE = 6.0


FEATURES = (
    {
        "live": "change_15m",
        "history": "return_15m_pct",
        "label": "15dk fiyat hareketi",
        "weight": 0.60,
    },
    {
        "live": "change_1h",
        "history": "return_1h_pct",
        "label": "1 saatlik fiyat hareketi",
        "weight": 0.60,
    },
    {
        "live": "change_3h",
        "history": "return_3h_pct",
        "label": "3 saatlik fiyat hareketi",
        "weight": 0.60,
    },
    {
        "live": "change_24h",
        "history": "return_24h_pct",
        "label": "24 saatlik fiyat hareketi",
        "weight": 0.45,
    },
    {
        "live": "btc_relative_24h",
        "history": "excess_vs_btc_24h_pct",
        "label": "BTC'ye gore guc",
        "weight": 0.75,
    },
    {
        "live": "volume_z_15m",
        "history": "volume_z_15m",
        "label": "15dk hacim anomalisi",
        "weight": 1.00,
    },
    {
        "live": "trade_z_15m",
        "history": "trade_z_15m",
        "label": "islem sayisi anomalisi",
        "weight": 1.00,
    },
    {
        "live": "return_z_15m",
        "history": "return_z_15m",
        "label": "fiyat anomalisi",
        "weight": 1.00,
    },
    {
        "live": "volume_mult_15m",
        "history": "volume_mult_15m",
        "label": "15dk hacim carpani",
        "weight": 0.90,
    },
    {
        "live": "volume_mult_1h",
        "history": "volume_mult_1h",
        "label": "1 saatlik hacim carpani",
        "weight": 0.90,
    },
    {
        "live": "taker_buy_ratio_15m",
        "history": "taker_buy_ratio_15m",
        "label": "agresif alici orani",
        "weight": 0.75,
    },
)


def utc_now():
    return datetime.now(
        timezone.utc
    ).isoformat()


def open_db():
    connection = sqlite3.connect(
        DB_FILE,
        timeout=60,
    )

    connection.row_factory = sqlite3.Row

    connection.execute(
        "PRAGMA busy_timeout=60000"
    )

    connection.execute(
        "PRAGMA journal_mode=WAL"
    )

    connection.execute(
        "PRAGMA synchronous=NORMAL"
    )

    return connection


def numeric(value):
    if value is None:
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(number):
        return None

    return number


def median(values):
    clean = [
        numeric(value)
        for value in values
    ]

    clean = [
        value
        for value in clean
        if value is not None
    ]

    if not clean:
        return None

    return statistics.median(
        clean
    )


def percentile(
    values,
    fraction,
):
    clean = sorted(
        numeric(value)
        for value in values
        if numeric(value) is not None
    )

    if not clean:
        return None

    if len(clean) == 1:
        return clean[0]

    position = (
        len(clean) - 1
    ) * fraction

    lower_index = int(
        math.floor(position)
    )

    upper_index = int(
        math.ceil(position)
    )

    if lower_index == upper_index:
        return clean[
            lower_index
        ]

    lower_value = clean[
        lower_index
    ]

    upper_value = clean[
        upper_index
    ]

    weight = (
        position
        - lower_index
    )

    return (
        lower_value
        + (
            upper_value
            - lower_value
        )
        * weight
    )


def robust_scale(values):
    clean = [
        numeric(value)
        for value in values
    ]

    clean = [
        value
        for value in clean
        if value is not None
    ]

    if not clean:
        return None

    center = statistics.median(
        clean
    )

    absolute_deviations = [
        abs(
            value
            - center
        )
        for value in clean
    ]

    mad = statistics.median(
        absolute_deviations
    )

    if mad > 1e-9:
        return 1.4826 * mad

    first_quartile = percentile(
        clean,
        0.25,
    )

    third_quartile = percentile(
        clean,
        0.75,
    )

    if (
        first_quartile is not None
        and third_quartile is not None
    ):
        interquartile_range = (
            third_quartile
            - first_quartile
        )

        if interquartile_range > 1e-9:
            return (
                interquartile_range
                / 1.349
            )

    if len(clean) >= 2:
        deviation = statistics.pstdev(
            clean
        )

        if deviation > 1e-9:
            return deviation

    return max(
        abs(center) * 0.10,
        0.01,
    )


def create_bridge_table(
    connection,
):
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS winner_bridge_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_time_utc TEXT NOT NULL,
            config_version TEXT NOT NULL,
            research_version TEXT NOT NULL,
            bridge_version TEXT NOT NULL,
            symbol TEXT NOT NULL,
            stage TEXT,
            avci_score INTEGER,
            best_offset_hours INTEGER,
            winner_similarity_pct REAL,
            reference_fit_pct REAL,
            classification TEXT,
            winner_distance REAL,
            control_distance REAL,
            winner_sample_count INTEGER,
            control_sample_count INTEGER,
            compared_feature_count INTEGER,
            strong_features_json TEXT,
            weak_features_json TEXT,
            details_json TEXT,
            created_at_utc TEXT,
            UNIQUE(
                scan_time_utc,
                config_version,
                research_version,
                bridge_version,
                symbol
            )
        );

        CREATE INDEX IF NOT EXISTS idx_winner_bridge_scan
        ON winner_bridge_scores(
            scan_time_utc,
            classification,
            winner_similarity_pct
        );

        CREATE INDEX IF NOT EXISTS idx_winner_bridge_symbol
        ON winner_bridge_scores(
            symbol,
            scan_time_utc
        );
        """
    )


def latest_research_version(
    connection,
):
    row = connection.execute(
        """
        SELECT
            research_version,
            MAX(analyzed_at_utc) AS latest_time
        FROM winner_events
        WHERE subject_class = 'WINNER'
          AND event_status = 'CLOSED'
          AND research_version IS NOT NULL
        GROUP BY research_version
        ORDER BY latest_time DESC
        LIMIT 1
        """
    ).fetchone()

    if row is None:
        return None

    return row[
        "research_version"
    ]


def latest_scan(
    connection,
):
    return connection.execute(
        """
        SELECT *
        FROM scans
        ORDER BY scan_time_utc DESC
        LIMIT 1
        """
    ).fetchone()


def latest_candidates(
    connection,
    scan,
):
    return connection.execute(
        """
        SELECT *
        FROM features
        WHERE scan_time_utc = ?
          AND config_version = ?
          AND selection_class = 'CANDIDATE'
          AND COALESCE(climax_risk, 0) = 0
        ORDER BY score DESC, symbol ASC
        """,
        (
            scan["scan_time_utc"],
            scan["config_version"],
        ),
    ).fetchall()


def historical_rows(
    connection,
    research_version,
):
    return connection.execute(
        """
        SELECT
            pre.*,
            events.matched_winner_event_id
        FROM pre_event_features pre
        JOIN winner_events events
          ON events.event_id = pre.event_id
         AND events.research_version =
             pre.research_version
        WHERE pre.research_version = ?
          AND events.event_status = 'CLOSED'
          AND (
                pre.subject_class = 'WINNER'
                OR (
                    pre.subject_class =
                        'NEAR_MATCH_CONTROL'
                    AND
                    events.matched_winner_event_id
                        IS NOT NULL
                )
          )
        ORDER BY
            pre.offset_hours DESC,
            pre.subject_class,
            pre.event_id
        """,
        (
            research_version,
        ),
    ).fetchall()


def group_historical_rows(rows):
    grouped = {}

    for offset in OFFSETS_HOURS:
        grouped[offset] = {
            "WINNER": [],
            "NEAR_MATCH_CONTROL": [],
        }

    for row in rows:
        offset = int(
            row["offset_hours"]
        )

        subject_class = row[
            "subject_class"
        ]

        if offset not in grouped:
            continue

        if subject_class not in grouped[
            offset
        ]:
            continue

        grouped[
            offset
        ][
            subject_class
        ].append(
            dict(row)
        )

    return grouped


def unique_event_count(rows):
    return len({
        row["event_id"]
        for row in rows
    })


def build_offset_reference(
    offset,
    grouped_rows,
):
    winners = grouped_rows[
        offset
    ][
        "WINNER"
    ]

    controls = grouped_rows[
        offset
    ][
        "NEAR_MATCH_CONTROL"
    ]

    winner_count = unique_event_count(
        winners
    )

    control_count = unique_event_count(
        controls
    )

    if (
        winner_count
        < MIN_CLASS_SAMPLES
        or control_count
        < MIN_CLASS_SAMPLES
    ):
        return None

    feature_references = {}

    for definition in FEATURES:
        history_key = definition[
            "history"
        ]

        winner_values = [
            row.get(history_key)
            for row in winners
        ]

        control_values = [
            row.get(history_key)
            for row in controls
        ]

        winner_values = [
            numeric(value)
            for value in winner_values
        ]

        control_values = [
            numeric(value)
            for value in control_values
        ]

        winner_values = [
            value
            for value in winner_values
            if value is not None
        ]

        control_values = [
            value
            for value in control_values
            if value is not None
        ]

        if (
            len(winner_values)
            < MIN_CLASS_SAMPLES
            or len(control_values)
            < MIN_CLASS_SAMPLES
        ):
            continue

        scale = robust_scale(
            winner_values
            + control_values
        )

        if (
            scale is None
            or scale <= 1e-12
        ):
            continue

        feature_references[
            definition["live"]
        ] = {
            "label":
                definition["label"],

            "weight":
                float(
                    definition["weight"]
                ),

            "winner_median":
                statistics.median(
                    winner_values
                ),

            "control_median":
                statistics.median(
                    control_values
                ),

            "scale":
                scale,

            "winner_values":
                len(winner_values),

            "control_values":
                len(control_values),
        }

    if not feature_references:
        return None

    return {
        "offset_hours":
            offset,

        "winner_count":
            winner_count,

        "control_count":
            control_count,

        "features":
            feature_references,
    }


def compare_candidate(
    candidate,
    reference,
):
    total_weight = 0.0
    winner_total = 0.0
    control_total = 0.0
    feature_details = []

    for definition in FEATURES:
        live_key = definition[
            "live"
        ]

        feature_reference = (
            reference[
                "features"
            ].get(
                live_key
            )
        )

        if feature_reference is None:
            continue

        candidate_value = numeric(
            candidate[
                live_key
            ]
        )

        if candidate_value is None:
            continue

        scale = feature_reference[
            "scale"
        ]

        winner_distance = min(
            MAX_STANDARD_DISTANCE,
            abs(
                candidate_value
                - feature_reference[
                    "winner_median"
                ]
            )
            / scale,
        )

        control_distance = min(
            MAX_STANDARD_DISTANCE,
            abs(
                candidate_value
                - feature_reference[
                    "control_median"
                ]
            )
            / scale,
        )

        weight = feature_reference[
            "weight"
        ]

        total_weight += weight

        winner_total += (
            winner_distance
            * weight
        )

        control_total += (
            control_distance
            * weight
        )

        feature_details.append({
            "key":
                live_key,

            "label":
                feature_reference[
                    "label"
                ],

            "candidate_value":
                candidate_value,

            "winner_median":
                feature_reference[
                    "winner_median"
                ],

            "control_median":
                feature_reference[
                    "control_median"
                ],

            "winner_distance":
                winner_distance,

            "control_distance":
                control_distance,

            "winner_edge":
                control_distance
                - winner_distance,

            "weight":
                weight,
        })

    if (
        total_weight <= 0
        or len(feature_details) < 4
    ):
        return None

    winner_distance = (
        winner_total
        / total_weight
    )

    control_distance = (
        control_total
        / total_weight
    )

    distance_edge = (
        control_distance
        - winner_distance
    )

    winner_similarity = (
        50.0
        + 50.0
        * math.tanh(
            distance_edge
            / 2.0
        )
    )

    nearest_distance = min(
        winner_distance,
        control_distance,
    )

    reference_fit = (
        100.0
        * math.exp(
            -nearest_distance
            / 3.0
        )
    )

    strong_features = sorted(
        feature_details,
        key=lambda item:
            item["winner_edge"],
        reverse=True,
    )[:3]

    weak_features = sorted(
        feature_details,
        key=lambda item:
            item["winner_edge"],
    )[:3]

    return {
        "offset_hours":
            reference[
                "offset_hours"
            ],

        "winner_count":
            reference[
                "winner_count"
            ],

        "control_count":
            reference[
                "control_count"
            ],

        "winner_distance":
            winner_distance,

        "control_distance":
            control_distance,

        "winner_similarity_pct":
            max(
                0.0,
                min(
                    100.0,
                    winner_similarity,
                ),
            ),

        "reference_fit_pct":
            max(
                0.0,
                min(
                    100.0,
                    reference_fit,
                ),
            ),

        "feature_count":
            len(feature_details),

        "strong_features":
            strong_features,

        "weak_features":
            weak_features,

        "all_features":
            feature_details,
    }


def choose_best_comparison(
    comparisons,
):
    if not comparisons:
        return None

    return max(
        comparisons,
        key=lambda result: (
            result[
                "reference_fit_pct"
            ],
            -min(
                result[
                    "winner_distance"
                ],
                result[
                    "control_distance"
                ],
            ),
            -result[
                "offset_hours"
            ],
        ),
    )


def classify(result):
    if result is None:
        return "YETERSIZ_VERI"

    similarity = result[
        "winner_similarity_pct"
    ]

    fit = result[
        "reference_fit_pct"
    ]

    if fit < 35.0:
        return "REFERANS_DISI"

    if similarity >= 65.0:
        return "KAZANANA_BENZER"

    if similarity <= 35.0:
        return "KONTROLE_BENZER"

    return "KARMA"


def compact_feature_rows(rows):
    return [
        {
            "key":
                row["key"],

            "label":
                row["label"],

            "candidate_value":
                round(
                    row["candidate_value"],
                    6,
                ),

            "winner_median":
                round(
                    row["winner_median"],
                    6,
                ),

            "control_median":
                round(
                    row["control_median"],
                    6,
                ),

            "winner_edge":
                round(
                    row["winner_edge"],
                    6,
                ),
        }
        for row in rows
    ]


def save_result(
    connection,
    scan,
    research_version,
    candidate,
    result,
):
    classification = classify(
        result
    )

    if result is None:
        values = {
            "offset_hours": None,
            "winner_similarity_pct": None,
            "reference_fit_pct": None,
            "winner_distance": None,
            "control_distance": None,
            "winner_count": 0,
            "control_count": 0,
            "feature_count": 0,
            "strong_features": [],
            "weak_features": [],
            "all_features": [],
        }

    else:
        values = result

    connection.execute(
        """
        INSERT INTO winner_bridge_scores (
            scan_time_utc,
            config_version,
            research_version,
            bridge_version,
            symbol,
            stage,
            avci_score,
            best_offset_hours,
            winner_similarity_pct,
            reference_fit_pct,
            classification,
            winner_distance,
            control_distance,
            winner_sample_count,
            control_sample_count,
            compared_feature_count,
            strong_features_json,
            weak_features_json,
            details_json,
            created_at_utc
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        ON CONFLICT(
            scan_time_utc,
            config_version,
            research_version,
            bridge_version,
            symbol
        )
        DO UPDATE SET
            stage =
                excluded.stage,

            avci_score =
                excluded.avci_score,

            best_offset_hours =
                excluded.best_offset_hours,

            winner_similarity_pct =
                excluded.winner_similarity_pct,

            reference_fit_pct =
                excluded.reference_fit_pct,

            classification =
                excluded.classification,

            winner_distance =
                excluded.winner_distance,

            control_distance =
                excluded.control_distance,

            winner_sample_count =
                excluded.winner_sample_count,

            control_sample_count =
                excluded.control_sample_count,

            compared_feature_count =
                excluded.compared_feature_count,

            strong_features_json =
                excluded.strong_features_json,

            weak_features_json =
                excluded.weak_features_json,

            details_json =
                excluded.details_json,

            created_at_utc =
                excluded.created_at_utc
        """,
        (
            scan["scan_time_utc"],
            scan["config_version"],
            research_version,
            BRIDGE_VERSION,
            candidate["symbol"],
            candidate["stage"],
            int(
                candidate["score"]
                or 0
            ),
            values["offset_hours"],
            values[
                "winner_similarity_pct"
            ],
            values[
                "reference_fit_pct"
            ],
            classification,
            values[
                "winner_distance"
            ],
            values[
                "control_distance"
            ],
            values[
                "winner_count"
            ],
            values[
                "control_count"
            ],
            values[
                "feature_count"
            ],
            json.dumps(
                compact_feature_rows(
                    values[
                        "strong_features"
                    ]
                ),
                ensure_ascii=False,
                sort_keys=True,
            ),
            json.dumps(
                compact_feature_rows(
                    values[
                        "weak_features"
                    ]
                ),
                ensure_ascii=False,
                sort_keys=True,
            ),
            json.dumps(
                {
                    "observational_only": True,
                    "thresholds_changed": False,
                    "all_features": (
                        compact_feature_rows(
                            values[
                                "all_features"
                            ]
                        )
                    ),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            utc_now(),
        ),
    )

    return classification


def print_result(
    rank,
    candidate,
    result,
):
    classification = classify(
        result
    )

    print(
        f"{rank}. {candidate['symbol']} "
        f"| {classification}"
    )

    if result is None:
        print(
            "   Karsilastirma icin "
            "yeterli ortak veri yok."
        )
        return

    print(
        "   Gecmis kazanan benzerligi: "
        f"%{result['winner_similarity_pct']:.1f}"
    )

    print(
        "   Referans uyumu: "
        f"%{result['reference_fit_pct']:.1f}"
    )

    print(
        "   En yakin tarihsel pencere: "
        f"hareketten {result['offset_hours']} "
        "saat once"
    )

    print(
        "   Orneklem: "
        f"{result['winner_count']} kazanan "
        f"| {result['control_count']} kontrol"
    )

    strong_labels = [
        row["label"]
        for row in result[
            "strong_features"
        ]
        if row[
            "winner_edge"
        ] > 0
    ]

    weak_labels = [
        row["label"]
        for row in result[
            "weak_features"
        ]
        if row[
            "winner_edge"
        ] < 0
    ]

    if strong_labels:
        print(
            "   Kazanana benzeyen taraflar: "
            + ", ".join(
                strong_labels
            )
        )

    if weak_labels:
        print(
            "   Kontrole benzeyen taraflar: "
            + ", ".join(
                weak_labels
            )
        )


def main():
    connection = open_db()

    try:
        create_bridge_table(
            connection
        )

        research_version = (
            latest_research_version(
                connection
            )
        )

        if research_version is None:
            print(
                "Winner Bridge calismadi: "
                "CLOSED Winner Anatomy "
                "verisi bulunamadi."
            )
            return

        scan = latest_scan(
            connection
        )

        if scan is None:
            print(
                "Winner Bridge calismadi: "
                "Avci 2 taramasi bulunamadi."
            )
            return

        if scan[
            "health_status"
        ] == "INVALID":
            print(
                "Winner Bridge calismadi: "
                "son Avci 2 taramasi INVALID."
            )
            return

        candidates = latest_candidates(
            connection,
            scan,
        )

        if not candidates:
            print(
                "Winner Bridge: "
                "son taramada aday yok."
            )
            return

        rows = historical_rows(
            connection,
            research_version,
        )

        grouped = group_historical_rows(
            rows
        )

        references = []

        for offset in OFFSETS_HOURS:
            reference = (
                build_offset_reference(
                    offset,
                    grouped,
                )
            )

            if reference is not None:
                references.append(
                    reference
                )

        if not references:
            print(
                "Winner Bridge calismadi: "
                "kazanan ve kontrol orneklemi "
                "henuz yeterli degil."
            )
            return

        print(
            "=" * 80
        )

        print(
            "BINANCE AVCI 2 "
            "- WINNER ANATOMY KOPRUSU"
        )

        print(
            f"Bridge: {BRIDGE_VERSION}"
        )

        print(
            "Research: "
            f"{research_version}"
        )

        print(
            "Tarama: "
            f"{scan['scan_time_utc']}"
        )

        print(
            "Aday sayisi: "
            f"{len(candidates)}"
        )

        print(
            "Kullanilan tarihsel "
            f"pencere sayisi: {len(references)}"
        )

        print(
            "=" * 80
        )

        for rank, candidate in enumerate(
            candidates,
            start=1,
        ):
            comparisons = []

            for reference in references:
                comparison = (
                    compare_candidate(
                        candidate,
                        reference,
                    )
                )

                if comparison is not None:
                    comparisons.append(
                        comparison
                    )

            best_result = (
                choose_best_comparison(
                    comparisons
                )
            )

            save_result(
                connection,
                scan,
                research_version,
                candidate,
                best_result,
            )

            print_result(
                rank,
                candidate,
                best_result,
            )

            print(
                "-" * 80
            )

        connection.commit()

        print(
            "NOT: Bu sonuc olasilik veya "
            "alim sinyali degildir."
        )

        print(
            "Avci 2 kurallari ve esikleri "
            "degistirilmedi."
        )

        print(
            "=" * 80
        )

    finally:
        connection.close()


if __name__ == "__main__":
    main()
