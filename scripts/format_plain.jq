# Plain-text briefing, for a --no-agent cron job: no model is called, so this
# costs nothing to run forever.
#
# Plain text on purpose. Telegram turns every backtick into its own code
# block, which shreds a briefing into confetti.
#
#   jq -r -f format_plain.jq report.json

def sign: if . >= 0 then "+" else "" end;

def pct_cell:
  if .pct == null then "n/a"
  else "\(.pct | sign)\(.pct)%"
  end;

[
  "Commodities - " + (now | strflocaltime("%a %d %b")),
  "",
  (.quotes[] | "\(.label): \(.last)  (\(pct_cell))"),
  "",
  (if (.headlines | length) > 0
   then ["Headlines:"] + [.headlines[] | "- " + .] | join("\n")
   else empty end),
  (if (.errors | length) > 0
   then "\ncould not fetch: " + (.errors | join(", "))
   else empty end)
]
| join("\n")
