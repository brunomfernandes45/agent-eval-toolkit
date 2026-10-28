import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const repoRoot = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const outputDir = path.join(repoRoot, "outputs", "thesis_verification");
const previewDir = path.join(repoRoot, "tmp", "oracle_workbook_previews");
await fs.mkdir(outputDir, { recursive: true });
await fs.mkdir(previewDir, { recursive: true });

const workbook = Workbook.create();
const protocol = workbook.worksheets.add("Protocol");
const pilotRaw = workbook.worksheets.add("Pilot Raw");
const pilotOracle = workbook.worksheets.add("Pilot Oracle");
const percentile = workbook.worksheets.add("Percentile R7");
const comparator = workbook.worksheets.add("Comparator");
const economics = workbook.worksheets.add("Economic Eligibility");
const coverage = workbook.worksheets.add("Coverage");
const mappingRaw = workbook.worksheets.add("Mapping Raw");
const mappingOracle = workbook.worksheets.add("Mapping Oracle");
const tiers = workbook.worksheets.add("Evidence Tiers");

const navy = "#17365D";
const blue = "#D9EAF7";
const pale = "#F3F6F9";
const green = "#E2F0D9";
const amber = "#FFF2CC";
const red = "#FCE4D6";
const grey = "#667085";
const white = "#FFFFFF";

function styleTitle(sheet, range) {
  sheet.getRange(range).format = {
    fill: navy,
    font: { bold: true, color: white, size: 16 },
    verticalAlignment: "center",
  };
}

function styleHeader(sheet, range) {
  sheet.getRange(range).format = {
    fill: blue,
    font: { bold: true, color: navy },
    borders: { preset: "outside", style: "thin", color: "#9EADBA" },
    wrapText: true,
    verticalAlignment: "center",
  };
}

function styleSection(sheet, range) {
  sheet.getRange(range).format = {
    fill: pale,
    font: { bold: true, color: navy },
    borders: { preset: "outside", style: "thin", color: "#C7D0D9" },
  };
}

function finishSheet(sheet, usedRange, widths) {
  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(3);
  sheet.getRange(usedRange).format.font = { name: "Aptos", size: 10 };
  for (const [range, width] of widths) {
    sheet.getRange(range).format.columnWidth = width;
  }
}

protocol.getRange("A1:H2").merge();
protocol.getRange("A1").values = [["Implementation-Independent Verification Oracle"]];
styleTitle(protocol, "A1:H2");
protocol.getRange("A4:B13").values = [
  ["Purpose", "Transparent, toolkit-independent calculations for the frozen conformance and enterprise-onboarding protocol."],
  ["Method", "Visible spreadsheet formulas over hand-authored raw fixture values; no toolkit code is imported."],
  ["Claim boundary", "Establishes implementation correctness under the tested contract, not enterprise effectiveness or causal impact."],
  ["Percentile rule", "R-7 inclusive quantile: h = 1 + (n - 1)p, with linear interpolation."],
  ["Coverage rule", "Population totals and comparator deltas are reported only at complete metric-specific coverage; partial values retain covered, eligible, and population denominators."],
  ["Cycle time", "Elapsed start-to-terminal time; never interpreted as active human effort."],
  ["Economic eligibility", "Completed, duration-valid, business-success (or approved quality) evidence plus explicit baseline and agent human effort."],
  ["Failed runs", "Zero benefit; their observed and declared per-run costs remain in portfolio cost."],
  ["Money", "Unrounded formula ledger; presentation values rounded to three decimals. Toolkit uses decimal arithmetic."],
  ["Source fixtures", "Synthetic run fixtures plus data/mapping_samples source profile, mapping specification, and activity log."],
];
styleSection(protocol, "A4:A13");
protocol.getRange("B4:B13").format = { wrapText: true, verticalAlignment: "top" };
protocol.getRange("A15:H18").merge();
protocol.getRange("A15").values = [[
  "Audit note: formula cells are deliberately visible and editable. Raw values are isolated from calculations. " +
    "Repeated executions use the same author and support deterministic repeatability only.",
]];
protocol.getRange("A15:H18").format = {
  fill: amber,
  font: { color: navy, italic: true },
  wrapText: true,
  verticalAlignment: "center",
  borders: { preset: "outside", style: "thin", color: "#C9B458" },
};
finishSheet(protocol, "A1:H18", [["A:A", 25], ["B:B", 85], ["C:H", 10]]);

pilotRaw.getRange("A1:O2").merge();
pilotRaw.getRange("A1").values = [["Pilot Fixture - Raw Run-Level Inputs"]];
styleTitle(pilotRaw, "A1:O2");
pilotRaw.getRange("A4:O4").values = [[
  "run_id",
  "status",
  "started_at_utc",
  "ended_at_utc",
  "duration_seconds",
  "input_tokens",
  "output_tokens",
  "observed_cost_usd",
  "quality_score",
  "business_success",
  "human_effort_seconds",
  "retry_count",
  "intervention_count",
  "tool_call_count",
  "configuration_id",
]];
styleHeader(pilotRaw, "A4:O4");
pilotRaw.getRange("A5:D7").values = [
  ["pilot-run-001", "completed", new Date("2026-04-01T09:00:00Z"), new Date("2026-04-01T09:01:01Z")],
  ["pilot-run-002", "completed", new Date("2026-04-01T10:15:00Z"), new Date("2026-04-01T10:17:08Z")],
  ["pilot-run-003", "failed", new Date("2026-04-01T11:40:00Z"), new Date("2026-04-01T11:40:41Z")],
];
pilotRaw.getRange("E5").formulas = [["=ROUND((D5-C5)*86400,3)"]];
pilotRaw.getRange("E5:E7").fillDown();
pilotRaw.getRange("F5:O7").values = [
  [3300, 820, 0.052, 0.94, true, 30, 0, 0, 2, "pilot-agent.v1"],
  [3800, 910, 0.063, 0.82, true, 60, 1, 1, 2, "pilot-agent.v1"],
  [900, 210, 0.011, null, false, 20, 0, 0, 1, "pilot-agent.v1"],
];
pilotRaw.getRange("C5:D7").format.numberFormat = "yyyy-mm-dd hh:mm:ss";
pilotRaw.getRange("E5:E7").format.numberFormat = "0.000";
pilotRaw.getRange("H5:H7").format.numberFormat = "$0.000";
pilotRaw.getRange("I5:I7").format.numberFormat = "0.000";
pilotRaw.getRange("A9:O11").merge();
pilotRaw.getRange("A9").values = [[
  "The timestamps, event-level token increments, costs, quality observations, status, and configuration identity are transcribed from the fixture. " +
    "Business-success and human-effort fields are explicit test-contract inputs used only for corrected economic eligibility; they are not inferred from elapsed duration.",
]];
pilotRaw.getRange("A9:O11").format = { fill: pale, wrapText: true, font: { color: grey, italic: true } };
finishSheet(pilotRaw, "A1:O11", [
  ["A:A", 20], ["B:B", 13], ["C:D", 22], ["E:E", 17], ["F:H", 16], ["I:O", 18],
]);

pilotOracle.getRange("A1:F2").merge();
pilotOracle.getRange("A1").values = [["Pilot Aggregate Oracle"]];
styleTitle(pilotOracle, "A1:F2");
pilotOracle.getRange("A4:F4").values = [["Metric", "Formula basis", "Expected", "Unit", "Coverage numerator", "Coverage denominator"]];
styleHeader(pilotOracle, "A4:F4");
pilotOracle.getRange("A5:B20").values = [
  ["run_count_total", "Count nonblank run IDs"],
  ["run_count_completed", "Count status = completed"],
  ["run_count_failed", "Count status = failed"],
  ["completion_rate", "completed / total"],
  ["failure_rate", "failed / total"],
  ["avg_duration_seconds", "Mean of run durations"],
  ["p50_duration_seconds", "R-7 oracle sheet"],
  ["p90_duration_seconds", "R-7 oracle sheet"],
  ["avg_retry_count", "Mean retry count"],
  ["avg_intervention_count", "Mean intervention count"],
  ["avg_tool_call_count", "Mean deduplicated tool calls"],
  ["avg_quality_score", "Mean observed quality only"],
  ["total_input_tokens", "Sum only at full coverage"],
  ["total_output_tokens", "Sum only at full coverage"],
  ["total_observed_cost_usd", "Sum only at full coverage"],
  ["configuration_identity_count", "Distinct expected identity (hand-checked)"],
];
pilotOracle.getRange("C5:C20").formulas = [
  ["=COUNTA('Pilot Raw'!$A$5:$A$7)"],
  ["=COUNTIF('Pilot Raw'!$B$5:$B$7,\"completed\")"],
  ["=COUNTIF('Pilot Raw'!$B$5:$B$7,\"failed\")"],
  ["=C6/C5"],
  ["=C7/C5"],
  ["=AVERAGE('Pilot Raw'!$E$5:$E$7)"],
  ["='Percentile R7'!$B$14"],
  ["='Percentile R7'!$E$14"],
  ["=AVERAGE('Pilot Raw'!$L$5:$L$7)"],
  ["=AVERAGE('Pilot Raw'!$M$5:$M$7)"],
  ["=AVERAGE('Pilot Raw'!$N$5:$N$7)"],
  ["=AVERAGE('Pilot Raw'!$I$5:$I$7)"],
  ["=IF(COUNTA('Pilot Raw'!$F$5:$F$7)=C5,SUM('Pilot Raw'!$F$5:$F$7),NA())"],
  ["=IF(COUNTA('Pilot Raw'!$G$5:$G$7)=C5,SUM('Pilot Raw'!$G$5:$G$7),NA())"],
  ["=IF(COUNTA('Pilot Raw'!$H$5:$H$7)=C5,SUM('Pilot Raw'!$H$5:$H$7),NA())"],
  ["=1"],
];
pilotOracle.getRange("D5:D20").values = [["runs"], ["runs"], ["runs"], ["rate"], ["rate"], ["seconds"], ["seconds"], ["seconds"], ["count/run"], ["count/run"], ["count/run"], ["score"], ["tokens"], ["tokens"], ["USD"], ["configurations"]];
pilotOracle.getRange("E5:E20").formulas = [
  ["=C5"], ["=C5"], ["=C5"], ["=C5"], ["=C5"], ["=COUNTA('Pilot Raw'!$E$5:$E$7)"], ["=COUNTA('Pilot Raw'!$E$5:$E$7)"], ["=COUNTA('Pilot Raw'!$E$5:$E$7)"], ["=COUNTA('Pilot Raw'!$L$5:$L$7)"], ["=COUNTA('Pilot Raw'!$M$5:$M$7)"], ["=COUNTA('Pilot Raw'!$N$5:$N$7)"], ["=COUNTA('Pilot Raw'!$I$5:$I$7)"], ["=COUNTA('Pilot Raw'!$F$5:$F$7)"], ["=COUNTA('Pilot Raw'!$G$5:$G$7)"], ["=COUNTA('Pilot Raw'!$H$5:$H$7)"], ["=C5"],
];
pilotOracle.getRange("F5:F20").formulas = Array.from({ length: 16 }, () => ["=$C$5"]);
pilotOracle.getRange("C8:C9").format.numberFormat = "0.000";
pilotOracle.getRange("C10:C16").format.numberFormat = "0.000";
pilotOracle.getRange("C17:C18").format.numberFormat = "0";
pilotOracle.getRange("C19").format.numberFormat = "$0.000";
pilotOracle.getRange("E5:F20").format.numberFormat = "0";
finishSheet(pilotOracle, "A1:F20", [["A:A", 31], ["B:B", 38], ["C:C", 17], ["D:D", 16], ["E:F", 20]]);

percentile.getRange("A1:F2").merge();
percentile.getRange("A1").values = [["R-7 Percentile Derivation"]];
styleTitle(percentile, "A1:F2");
percentile.getRange("A4:F4").values = [["p50 label", "p50 value", "p50 formula", "p90 label", "p90 value", "p90 formula"]];
styleHeader(percentile, "A4:F4");
const percentileLabels = ["n", "p", "h = 1 + (n-1)p", "j = floor(h)", "g = h-j", "x[j]", "x[j+1]", "result = x[j] + g(x[j+1]-x[j])"];
for (let i = 0; i < percentileLabels.length; i += 1) {
  percentile.getRange(`A${5 + i}`).values = [[percentileLabels[i]]];
  percentile.getRange(`D${5 + i}`).values = [[percentileLabels[i]]];
}
percentile.getRange("B5:B12").formulas = [
  ["=COUNTA('Pilot Raw'!$E$5:$E$7)"], ["=0.5"], ["=1+(B5-1)*B6"], ["=INT(B7)"], ["=B7-B8"], ["=SMALL('Pilot Raw'!$E$5:$E$7,B8)"], ["=SMALL('Pilot Raw'!$E$5:$E$7,MIN(B8+1,B5))"], ["=B10+B9*(B11-B10)"],
];
percentile.getRange("E5:E12").formulas = [
  ["=COUNTA('Pilot Raw'!$E$5:$E$7)"], ["=0.9"], ["=1+(E5-1)*E6"], ["=INT(E7)"], ["=E7-E8"], ["=SMALL('Pilot Raw'!$E$5:$E$7,E8)"], ["=SMALL('Pilot Raw'!$E$5:$E$7,MIN(E8+1,E5))"], ["=E10+E9*(E11-E10)"],
];
percentile.getRange("C5:C12").values = [["COUNT"], ["declared probability"], ["rank"], ["lower rank"], ["fraction"], ["ordered duration"], ["next ordered duration"], ["linear interpolation"]];
percentile.getRange("F5:F12").values = [["COUNT"], ["declared probability"], ["rank"], ["lower rank"], ["fraction"], ["ordered duration"], ["next ordered duration"], ["linear interpolation"]];
percentile.getRange("A14:B14").values = [["p50_duration_seconds", null]];
percentile.getRange("B14").formulas = [["=B12"]];
percentile.getRange("D14:E14").values = [["p90_duration_seconds", null]];
percentile.getRange("E14").formulas = [["=E12"]];
styleSection(percentile, "A14:B14");
styleSection(percentile, "D14:E14");
percentile.getRange("B5:B14").format.numberFormat = "0.000";
percentile.getRange("E5:E14").format.numberFormat = "0.000";
finishSheet(percentile, "A1:F14", [["A:A", 31], ["B:B", 16], ["C:C", 24], ["D:D", 31], ["E:E", 16], ["F:F", 24]]);

comparator.getRange("A1:I2").merge();
comparator.getRange("A1").values = [["Synthetic Comparator Arithmetic - Readiness Only"]];
styleTitle(comparator, "A1:I2");
comparator.getRange("A4:I4").values = [["configuration", "duration_seconds", "quality_score", "input_tokens", "output_tokens", "duration_delta", "quality_delta", "input_delta", "output_delta"]];
styleHeader(comparator, "A4:I4");
comparator.getRange("A5:E6").values = [
  ["control-agent.v1", 300, 0.84, 900, 180],
  ["treatment-agent.v1", 180, 0.90, 800, 160],
];
comparator.getRange("F6:I6").formulas = [["=B6-B5", "=C6-C5", "=D6-D5", "=E6-E5"]];
comparator.getRange("F5:I5").values = [[null, null, null, null]];
comparator.getRange("B5:B6").format.numberFormat = "0.000";
comparator.getRange("C5:C6").format.numberFormat = "0.000";
comparator.getRange("F6:I6").format.numberFormat = "0.000";
comparator.getRange("A8:I10").merge();
comparator.getRange("A8").values = [[
  "Interpretation: these exact deltas validate comparator arithmetic and sign conventions only. The synthetic scenario supplies neither random assignment nor naturalistic enterprise evidence, so attained claim strength is capped at conformance/readiness.",
]];
comparator.getRange("A8:I10").format = { fill: amber, wrapText: true, font: { color: navy, italic: true } };
finishSheet(comparator, "A1:I10", [["A:A", 24], ["B:I", 17]]);

economics.getRange("A1:N2").merge();
economics.getRange("A1").values = [["Corrected Economic Eligibility Oracle"]];
styleTitle(economics, "A1:N2");
economics.getRange("A4:B9").values = [
  ["Assumption", "Value"],
  ["manual_baseline_cycle_time_seconds", 420],
  ["manual_baseline_human_effort_seconds", 300],
  ["loaded_labor_rate_usd_per_hour", 68],
  ["overhead_cost_usd_per_run", 0.15],
  ["tooling_cost_usd_per_run", 0.02],
];
styleHeader(economics, "A4:B4");
economics.getRange("B7:B9").format.numberFormat = "$0.000";
economics.getRange("A11:N11").values = [[
  "run_id", "status", "business_success", "duration_seconds", "human_effort_seconds", "observed_cost_usd", "benefit_eligible", "estimated_cycle_time_difference", "estimated_human_effort_saved", "labor_value_benefit_usd", "overhead_usd", "tooling_usd", "all_run_cost_usd", "net_value_usd",
]];
styleHeader(economics, "A11:N11");
economics.getRange("A12:F14").formulas = [
  ["='Pilot Raw'!A5", "='Pilot Raw'!B5", "='Pilot Raw'!J5", "='Pilot Raw'!E5", "='Pilot Raw'!K5", "='Pilot Raw'!H5"],
  ["='Pilot Raw'!A6", "='Pilot Raw'!B6", "='Pilot Raw'!J6", "='Pilot Raw'!E6", "='Pilot Raw'!K6", "='Pilot Raw'!H6"],
  ["='Pilot Raw'!A7", "='Pilot Raw'!B7", "='Pilot Raw'!J7", "='Pilot Raw'!E7", "='Pilot Raw'!K7", "='Pilot Raw'!H7"],
];
economics.getRange("G12").formulas = [["=AND(B12=\"completed\",C12=TRUE,D12<>\"\",E12<>\"\")"]];
economics.getRange("G12:G14").fillDown();
economics.getRange("H12").formulas = [["=IF(AND(B12=\"completed\",D12<>\"\"),$B$5-D12,\"\")"]];
economics.getRange("H12:H14").fillDown();
economics.getRange("I12").formulas = [["=IF(G12,$B$6-E12,0)"]];
economics.getRange("I12:I14").fillDown();
economics.getRange("J12").formulas = [["=IF(G12,MAX(0,I12)/3600*$B$7,0)"]];
economics.getRange("J12:J14").fillDown();
economics.getRange("K12").formulas = [["=$B$8"]];
economics.getRange("K12:K14").fillDown();
economics.getRange("L12").formulas = [["=$B$9"]];
economics.getRange("L12:L14").fillDown();
economics.getRange("M12").formulas = [["=F12+K12+L12"]];
economics.getRange("M12:M14").fillDown();
economics.getRange("N12").formulas = [["=J12-M12"]];
economics.getRange("N12:N14").fillDown();
economics.getRange("A16:D22").values = [
  ["Portfolio metric", "Formula result", "Presentation (3 d.p.)", "Eligibility/coverage"],
  ["eligible_run_count", null, null, "completed + success + explicit effort"],
  ["ineligible_run_count", null, null, "included - eligible"],
  ["total_labor_value_benefit_usd", null, null, "eligible runs only"],
  ["total_all_run_cost_usd", null, null, "all included runs; requires 3/3 observed costs"],
  ["total_net_value_usd", null, null, "benefit minus all-run cost; withheld if cost incomplete"],
  ["average_net_value_usd_per_included_run", null, null, "portfolio net / included runs"],
];
styleHeader(economics, "A16:D16");
economics.getRange("B17:B22").formulas = [
  ["=IF(G12,1,0)+IF(G13,1,0)+IF(G14,1,0)"],
  ["=COUNTA(A12:A14)-B17"],
  ["=SUM(J12:J14)"],
  ["=IF(COUNTA(F12:F14)=COUNTA(A12:A14),SUM(M12:M14),\"\")"],
  ["=IF(COUNTA(F12:F14)=COUNTA(A12:A14),SUM(N12:N14),\"\")"],
  ["=B21/COUNTA(A12:A14)"],
];
economics.getRange("C17:C22").formulas = [
  ["=B17"], ["=B18"], ["=ROUND(B19,3)"], ["=ROUND(B20,3)"], ["=ROUND(B21,3)"], ["=ROUND(B22,3)"],
];
economics.getRange("F12:F14").format.numberFormat = "$0.000";
economics.getRange("J12:N14").format.numberFormat = "$0.000000";
economics.getRange("B19:C22").format.numberFormat = "$0.000";
economics.getRange("A24:N27").merge();
economics.getRange("A24").values = [[
  "The failed run has benefit_eligible = FALSE and labor benefit = 0, while its observed, overhead, and tooling costs remain in the portfolio total. If any observed-cost cell is cleared, total cost and net value are blank (withheld) rather than treating missing cost as zero.",
]];
economics.getRange("A24:N27").format = { fill: green, wrapText: true, font: { color: navy, italic: true } };
finishSheet(economics, "A1:N27", [["A:A", 33], ["B:B", 19], ["C:C", 20], ["D:N", 20]]);

coverage.getRange("A1:G2").merge();
coverage.getRange("A1").values = [["Coverage and Withholding Oracle"]];
styleTitle(coverage, "A1:G2");
coverage.getRange("A4:G4").values = [["signal", "observed_n", "included_N", "coverage", "complete_total", "partial_observed_sum", "rule"]];
styleHeader(coverage, "A4:G4");
coverage.getRange("A5:A9").values = [["input_tokens"], ["output_tokens"], ["observed_cost_usd"], ["quality_score"], ["human_effort_seconds"]];
coverage.getRange("B5:B9").formulas = [
  ["=COUNTA('Pilot Raw'!$F$5:$F$7)"], ["=COUNTA('Pilot Raw'!$G$5:$G$7)"], ["=COUNTA('Pilot Raw'!$H$5:$H$7)"], ["=COUNTA('Pilot Raw'!$I$5:$I$7)"], ["=COUNTA('Pilot Raw'!$K$5:$K$7)"],
];
coverage.getRange("C5:C9").formulas = Array.from({ length: 5 }, () => ["=COUNTA('Pilot Raw'!$A$5:$A$7)"]);
coverage.getRange("D5").formulas = [["=B5/C5"]];
coverage.getRange("D5:D9").fillDown();
coverage.getRange("E5:E9").formulas = [
  ["=IF(B5=C5,SUM('Pilot Raw'!$F$5:$F$7),\"\")"], ["=IF(B6=C6,SUM('Pilot Raw'!$G$5:$G$7),\"\")"], ["=IF(B7=C7,SUM('Pilot Raw'!$H$5:$H$7),\"\")"], ["=IF(B8=C8,SUM('Pilot Raw'!$I$5:$I$7),\"\")"], ["=IF(B9=C9,SUM('Pilot Raw'!$K$5:$K$7),\"\")"],
];
coverage.getRange("F5:F9").formulas = [
  ["=SUM('Pilot Raw'!$F$5:$F$7)"], ["=SUM('Pilot Raw'!$G$5:$G$7)"], ["=SUM('Pilot Raw'!$H$5:$H$7)"], ["=SUM('Pilot Raw'!$I$5:$I$7)"], ["=SUM('Pilot Raw'!$K$5:$K$7)"],
];
coverage.getRange("G5:G9").values = Array.from({ length: 5 }, () => ["Complete total only at observed_n = included_N; otherwise report partial sum with n/N."]);
coverage.getRange("D5:D9").format.numberFormat = "0.0%";
coverage.getRange("E7:F7").format.numberFormat = "$0.000";
coverage.getRange("A11:G14").merge();
coverage.getRange("A11").values = [[
  "Quality coverage is 2/3: the workbook therefore exposes the observed two-run mean elsewhere with its denominator, but it does not treat the missing failed-run quality as zero. Human effort is present for all runs here to exercise corrected economic logic; failed status still prevents benefit.",
]];
coverage.getRange("A11:G14").format = { fill: red, wrapText: true, font: { color: navy, italic: true } };
finishSheet(coverage, "A1:G14", [["A:A", 25], ["B:F", 20], ["G:G", 70]]);

mappingRaw.getRange("A1:Q2").merge();
mappingRaw.getRange("A1").values = [["Enterprise-Onboarding Fixture - Raw Inputs"]];
styleTitle(mappingRaw, "A1:Q2");
mappingRaw.getRange("A4:I4").values = [[
  "activity_id", "timestamp_utc", "actor_role", "activity_type", "effort_minutes", "mapping_version", "issue_id", "outcome", "version_start_flag",
]];
styleHeader(mappingRaw, "A4:I4");
mappingRaw.getRange("A5:H8").values = [
  ["activity-001", new Date("2026-04-01T09:00:00Z"), "data-engineer", "source_review", 10, "v1", null, "profiled"],
  ["activity-002", new Date("2026-04-01T09:20:00Z"), "workflow-owner", "semantic_decision", 20, "v1", "MAP-001", "status semantics confirmed"],
  ["activity-003", new Date("2026-04-01T09:45:00Z"), "data-engineer", "mapping_change", 5, "v2", "MAP-001", "lookup added"],
  ["activity-004", new Date("2026-04-01T10:00:00Z"), "workflow-owner", "approval", 10, "v2", null, "approved"],
];
mappingRaw.getRange("I5").formulas = [["=1"]];
mappingRaw.getRange("I6").formulas = [["=IF(F6<>F5,1,0)"]];
mappingRaw.getRange("I6:I8").fillDown();
mappingRaw.getRange("B5:B8").format.numberFormat = "yyyy-mm-dd hh:mm:ss";

mappingRaw.getRange("K4:Q4").values = [[
  "canonical_signal", "required", "observed_count", "transformation", "tested", "semantic_status", "approval_status",
]];
styleHeader(mappingRaw, "K4:Q4");
mappingRaw.getRange("K5:Q8").values = [
  ["run_id", 1, 12, "direct", 1, "confirmed", "approved"],
  ["timestamp", 1, 12, "coercion", 1, "confirmed", "approved"],
  ["status", 1, 12, "lookup", 1, "confirmed", "approved"],
  ["quality_score", 0, 9, "direct", 1, "confirmed", "approved"],
];
mappingRaw.getRange("K10:L15").values = [
  ["source_profile_dimension", "value"],
  ["file_count", 2],
  ["record_count", 12],
  ["format_count", 2],
  ["field_count", 4],
  ["manual_review_record_count", 3],
];
styleHeader(mappingRaw, "K10:L10");
mappingRaw.getRange("N10:Q14").values = [
  ["approval_or_issue", "state", "count", "evidence"],
  ["workflow_boundary", "approved", 1, "boundary-review-001"],
  ["business_status", "approved", 1, "status-review-001"],
  ["privacy", "approved", 1, "privacy-review-001"],
  ["warning:resolved", "resolved", 1, "MAP-001"],
];
styleHeader(mappingRaw, "N10:Q10");
mappingRaw.getRange("A10:I13").merge();
mappingRaw.getRange("A10").values = [[
  "These hand-authored fixture values verify calculation mechanics only. They are not observations of enterprise onboarding burden. No source field values are retained in the profile.",
]];
mappingRaw.getRange("A10:I13").format = { fill: amber, wrapText: true, font: { color: navy, italic: true } };
finishSheet(mappingRaw, "A1:Q15", [["A:A", 18], ["B:B", 22], ["C:I", 18], ["J:J", 3], ["K:Q", 20]]);

mappingOracle.getRange("A1:G2").merge();
mappingOracle.getRange("A1").values = [["Mapping-Friction Dimensional Oracle"]];
styleTitle(mappingOracle, "A1:G2");
mappingOracle.getRange("A4:G4").values = [["dimension", "formula basis", "expected", "unit/status", "observed_n", "denominator_N", "interpretation"]];
styleHeader(mappingOracle, "A4:G4");
mappingOracle.getRange("A5:B21").values = [
  ["required_signal_count", "Count required mapping rows"],
  ["required_mapped_count", "Required rows not unmapped"],
  ["required_observed_count", "Required rows observed in source profile"],
  ["required_transformation_tested_count", "Required rows with tested=true"],
  ["required_semantically_approved_count", "Required rows confirmed and approved"],
  ["direct_transformations", "Count transformation=direct"],
  ["coercion_transformations", "Count transformation=coercion"],
  ["lookup_transformations", "Count transformation=lookup"],
  ["mapping_iteration_count", "Sum version-start flags"],
  ["semantic_decision_count", "Count semantic_decision activities"],
  ["approval_activity_count", "Count approval activities"],
  ["elapsed_onboarding_cycle_seconds", "Latest minus earliest activity timestamp"],
  ["effort_measurement_status", "Complete only if every activity has effort"],
  ["observed_effort_minutes", "Sum recorded effort"],
  ["complete_effort_minutes", "Total only when observed_n=N"],
  ["manual_review_records", "Source-profile count"],
  ["source_format_count", "Source-profile format count"],
];
mappingOracle.getRange("C5:C21").formulas = [
  ["=COUNTIF('Mapping Raw'!$L$5:$L$8,1)"],
  ["=COUNTIFS('Mapping Raw'!$L$5:$L$8,1,'Mapping Raw'!$N$5:$N$8,\"<>unmapped\")"],
  ["=COUNTIFS('Mapping Raw'!$L$5:$L$8,1,'Mapping Raw'!$M$5:$M$8,\">0\")"],
  ["=COUNTIFS('Mapping Raw'!$L$5:$L$8,1,'Mapping Raw'!$O$5:$O$8,1)"],
  ["=COUNTIFS('Mapping Raw'!$L$5:$L$8,1,'Mapping Raw'!$P$5:$P$8,\"confirmed\",'Mapping Raw'!$Q$5:$Q$8,\"approved\")"],
  ["=COUNTIF('Mapping Raw'!$N$5:$N$8,\"direct\")"],
  ["=COUNTIF('Mapping Raw'!$N$5:$N$8,\"coercion\")"],
  ["=COUNTIF('Mapping Raw'!$N$5:$N$8,\"lookup\")"],
  ["=SUM('Mapping Raw'!$I$5:$I$8)"],
  ["=COUNTIF('Mapping Raw'!$D$5:$D$8,\"semantic_decision\")"],
  ["=COUNTIF('Mapping Raw'!$D$5:$D$8,\"approval\")"],
  ["=ROUND((MAX('Mapping Raw'!$B$5:$B$8)-MIN('Mapping Raw'!$B$5:$B$8))*86400,3)"],
  ["=IF(COUNTA('Mapping Raw'!$E$5:$E$8)=0,\"not_recorded\",IF(COUNTA('Mapping Raw'!$E$5:$E$8)=COUNTA('Mapping Raw'!$A$5:$A$8),\"complete\",\"partial\"))"],
  ["=SUM('Mapping Raw'!$E$5:$E$8)"],
  ["=IF(COUNTA('Mapping Raw'!$E$5:$E$8)=COUNTA('Mapping Raw'!$A$5:$A$8),SUM('Mapping Raw'!$E$5:$E$8),\"\")"],
  ["='Mapping Raw'!$L$15"],
  ["='Mapping Raw'!$L$13"],
];
mappingOracle.getRange("D5:D21").values = [["signals"], ["signals"], ["signals"], ["signals"], ["signals"], ["mappings"], ["mappings"], ["mappings"], ["versions"], ["decisions"], ["approvals"], ["seconds"], ["status"], ["minutes"], ["minutes"], ["records"], ["formats"]];
mappingOracle.getRange("E5:F21").formulas = [
  ["=C5", "=C5"], ["=C6", "=C5"], ["=C7", "=C5"], ["=C8", "=C5"], ["=C9", "=C5"],
  ["=C10", "=COUNTA('Mapping Raw'!$K$5:$K$8)"], ["=C11", "=COUNTA('Mapping Raw'!$K$5:$K$8)"], ["=C12", "=COUNTA('Mapping Raw'!$K$5:$K$8)"],
  ["=C13", "=C13"], ["=C14", "=COUNTA('Mapping Raw'!$A$5:$A$8)"], ["=C15", "=COUNTA('Mapping Raw'!$A$5:$A$8)"],
  ["=COUNTA('Mapping Raw'!$B$5:$B$8)", "=COUNTA('Mapping Raw'!$A$5:$A$8)"], ["=COUNTA('Mapping Raw'!$E$5:$E$8)", "=COUNTA('Mapping Raw'!$A$5:$A$8)"],
  ["=COUNTA('Mapping Raw'!$E$5:$E$8)", "=COUNTA('Mapping Raw'!$A$5:$A$8)"], ["=COUNTA('Mapping Raw'!$E$5:$E$8)", "=COUNTA('Mapping Raw'!$A$5:$A$8)"],
  ["=C20", "='Mapping Raw'!$L$12"], ["=C21", "=C21"],
];
mappingOracle.getRange("G5:G21").values = Array.from({ length: 17 }, () => ["Separate dimension; no universal weighted friction score."]);
mappingOracle.getRange("C16:C18").format.numberFormat = "0.000";
mappingOracle.getRange("A23:G26").merge();
mappingOracle.getRange("A23").values = [[
  "Missing-log oracle: when no activity rows exist, elapsed cycle time and both effort totals are blank and effort status is not_recorded. Partial-log oracle: 35 recorded minutes over 3/4 activities remains a partial observed sum and complete_effort_minutes is blank.",
]];
mappingOracle.getRange("A23:G26").format = { fill: green, wrapText: true, font: { color: navy, italic: true } };
finishSheet(mappingOracle, "A1:G26", [["A:A", 38], ["B:B", 42], ["C:F", 18], ["G:G", 56]]);

tiers.getRange("A1:K2").merge();
tiers.getRange("A1").values = [["Evidence-Tier and Per-Metric Publication Oracle"]];
styleTitle(tiers, "A1:K2");
tiers.getRange("A4:K4").values = [["scenario", "governance_safe", "mapping_required", "mapping_readiness", "run_count", "usable_metric_count", "telemetry_passed", "quarantined_runs", "attained_tier", "minimum_tier", "minimum_met"]];
styleHeader(tiers, "A4:K4");
tiers.getRange("A5:H8").values = [
  ["privacy blocker", false, true, "blocked", 2, 3, true, 0],
  ["unresolved semantics", true, true, "provisional", 2, 3, true, 0],
  ["one quarantined run", true, false, "not_applicable", 2, 3, true, 1],
  ["complete requested evidence", true, false, "not_applicable", 2, 3, true, 0],
];
tiers.getRange("I5").formulas = [["=IF(B5=FALSE,\"blocked\",IF(AND(C5=TRUE,D5<>\"verified\"),\"mapping_only\",IF(OR(E5=0,F5=0),\"mapping_only\",IF(AND(G5=TRUE,H5=0),\"complete\",\"partial\"))))"]];
tiers.getRange("I5:I8").fillDown();
tiers.getRange("J5:J8").values = [["mapping_only"], ["mapping_only"], ["partial"], ["complete"]];
tiers.getRange("K5").formulas = [["=IF(I5=\"blocked\",0,IF(I5=\"mapping_only\",1,IF(I5=\"partial\",2,3)))>=IF(J5=\"mapping_only\",1,IF(J5=\"partial\",2,3))"]];
tiers.getRange("K5:K8").fillDown();

tiers.getRange("A11:H11").values = [["metric_family", "capability_supported", "covered_runs", "eligible_runs", "quarantined_runs", "population_denominator", "preconditions_passed", "metric_status"]];
styleHeader(tiers, "A11:H11");
tiers.getRange("A12:G17").values = [
  ["duration", true, 2, 2, 1, null, true],
  ["reliability", true, 2, 2, 1, null, true],
  ["quality", true, 0, 2, 0, null, true],
  ["observed_cost", false, 0, 2, 0, null, true],
  ["comparator", true, 2, 2, 1, null, false],
  ["economic_estimates", true, 2, 2, 1, null, false],
];
tiers.getRange("F12").formulas = [["=D12+E12"]];
tiers.getRange("F12:F17").fillDown();
tiers.getRange("H12").formulas = [["=IF(B12=FALSE,\"unsupported\",IF(G12=FALSE,\"withheld\",IF(C12=0,\"withheld\",IF(C12=F12,\"available\",\"partial\"))))"]];
tiers.getRange("H12:H17").fillDown();
tiers.getRange("A19:K22").merge();
tiers.getRange("A19").values = [[
  "Tier completeness and claim strength are independent. A synthetic fixture can attain complete readiness while its attained claim remains capped at conformance/readiness. Evidence tiers are ordinal publication states, not statistical confidence levels.",
]];
tiers.getRange("A19:K22").format = { fill: amber, wrapText: true, font: { color: navy, italic: true } };
finishSheet(tiers, "A1:K22", [["A:A", 30], ["B:H", 19], ["I:J", 19], ["K:K", 16]]);

for (const sheet of [pilotRaw, pilotOracle, percentile, comparator, economics, coverage, mappingRaw, mappingOracle, tiers]) {
  sheet.getRange("A4:Z40").format.verticalAlignment = "center";
}

const checks = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
});
await fs.writeFile(path.join(previewDir, "formula_error_scan.ndjson"), checks.ndjson ?? "", "utf8");

for (const sheetName of ["Protocol", "Pilot Raw", "Pilot Oracle", "Percentile R7", "Comparator", "Economic Eligibility", "Coverage", "Mapping Raw", "Mapping Oracle", "Evidence Tiers"]) {
  const preview = await workbook.render({ sheetName, autoCrop: "all", scale: 1.4, format: "png" });
  const safe = sheetName.toLowerCase().replaceAll(" ", "_");
  await fs.writeFile(path.join(previewDir, `${safe}.png`), new Uint8Array(await preview.arrayBuffer()));
}

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(path.join(outputDir, "implementation_independent_oracle.xlsx"));

const inspection = await workbook.inspect({
  kind: "table",
  sheetId: "Economic Eligibility",
  range: "A11:N22",
  include: "values,formulas",
  tableMaxRows: 20,
  tableMaxCols: 14,
});
await fs.writeFile(path.join(previewDir, "economic_inspection.ndjson"), inspection.ndjson ?? "", "utf8");

const mappingInspection = await workbook.inspect({
  kind: "table",
  sheetId: "Mapping Oracle",
  range: "A4:G26",
  include: "values,formulas",
  tableMaxRows: 30,
  tableMaxCols: 7,
});
const tierInspection = await workbook.inspect({
  kind: "table",
  sheetId: "Evidence Tiers",
  range: "A4:K22",
  include: "values,formulas",
  tableMaxRows: 25,
  tableMaxCols: 11,
});
await fs.writeFile(
  path.join(outputDir, "implementation_independent_oracle.xlsx.inspect.ndjson"),
  [inspection.ndjson ?? "", mappingInspection.ndjson ?? "", tierInspection.ndjson ?? ""].join("\n"),
  "utf8",
);
