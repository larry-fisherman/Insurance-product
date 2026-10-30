"""Repeatable synthetic regression evaluation; not a claim about broker documents or AI quality."""

import argparse
import json
import time
from pathlib import Path
from statistics import median
from uuid import uuid4

from app.demo import pdf
from app.documents import Block, classify, local_extract, parse_pdf
from app.domain.cgl import group_key, reconcile


def evaluate(count: int = 100) -> dict[str, object]:
    expected_facts = correct_facts = extracted_count = amount_total = amount_correct = traceable = 0
    true_conflicts = found_conflicts = false_conflicts = 0
    timings: list[float] = []
    for index in range(count):
        started = time.perf_counter()
        amount, alternate = str(100000 + index * 101), str(110000 + index * 101)
        basis = ["Annual gross sales", "Payroll", "Area square feet"][index % 3]
        basis_key, unit = [("sales", "money"), ("payroll", "money"), ("area", "square feet")][
            index % 3
        ]
        name = f"Fictional Regression Insured {index} LLC"
        lines = [
            "FICTIONAL CGL APPLICATION",
            "Legal insured name: " + name,
            "Business operations: Commercial office consulting",
            "Currency: USD",
            "Exposure period: 2027-01-01 to 2027-12-31",
            "Exposure status: estimate",
            basis + ": " + amount,
        ]
        blocks, _ = parse_pdf(pdf(lines))
        observations = []
        # Half the packets include a conflicting schedule; half have another operation/period.
        is_conflict = index % 2 == 0
        schedule = [
            Block(line, {"kind": "xlsx", "sheet": "Exposure", "range": f"A{i + 1}:B{i + 1}"})
            for i, line in enumerate(
                [
                    "Currency: USD",
                    "Exposure status: estimate",
                    "Exposure period: "
                    + ("2027-01-01 to 2027-12-31" if is_conflict else "2026-01-01 to 2026-12-31"),
                    basis + ": " + alternate,
                ]
            )
        ]
        expected = [
            ("insured.legal_name", name),
            ("insured.operations", "Commercial office consulting"),
            ("exposure.amount", amount),
            ("exposure.amount", alternate),
        ]
        found = []
        for source_blocks in (blocks, schedule):
            lookup = {b.id for b in source_blocks}
            for fact in local_extract(source_blocks, classify(source_blocks)):
                found.append((fact.field, fact.value))
                traceable += int(
                    bool(fact.source_ids) and all(ref in lookup for ref in fact.source_ids)
                )
                observations.append(
                    {
                        "id": str(uuid4()),
                        "group_key": group_key(fact.field, fact.context, fact.value),
                        "field": fact.field,
                        "value": fact.value,
                        "context": fact.context,
                    }
                )
                if fact.field == "exposure.amount":
                    amount_total += 1
                    amount_correct += int(
                        fact.value in (amount, alternate)
                        and fact.context["basis"] == basis_key
                        and fact.context["unit"] == unit
                    )
        expected_facts += len(expected)
        extracted_count += len(found)
        correct_facts += sum(item in expected for item in found)
        findings = reconcile(observations)
        true_conflicts += int(is_conflict)
        found_conflicts += int(is_conflict and len(findings) == 1)
        false_conflicts += int(not is_conflict and bool(findings))
        timings.append(time.perf_counter() - started)
    return {
        "corpus": "generated explicit-label regression cases v1; not independent held-out documents",
        "packets": count,
        "extraction_precision": correct_facts / max(extracted_count, 1),
        "extraction_recall": correct_facts / max(expected_facts, 1),
        "financial_and_exposure_amount_accuracy": amount_correct / max(amount_total, 1),
        "discrepancy_recall": found_conflicts / max(true_conflicts, 1),
        "discrepancy_precision": found_conflicts / max(found_conflicts + false_conflicts, 1),
        "source_reference_coverage": traceable / max(extracted_count, 1),
        "median_processing_seconds": round(median(timings), 4),
        "p95_processing_seconds": round(sorted(timings)[int(len(timings) * 0.95) - 1], 4),
        "external_provider_cost": "0: no provider requests",
        "unsupported_generated_claims": "Not measured by this extraction-only evaluation",
        "human_correction_rate": "Requires broker-reviewed pilot corpus",
        "cost_per_submission": "Requires infrastructure allocation and vendor usage",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(".artifacts/evaluation.json"))
    parser.add_argument("--packets", type=int, default=100)
    args = parser.parse_args()
    if not 10 <= args.packets <= 1000:
        parser.error("Use 10–1000 synthetic packets")
    result = evaluate(args.packets)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
