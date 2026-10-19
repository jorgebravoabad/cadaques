import json
import sys

from .grade import grade

for run_dir in sys.argv[1:]:
    g = grade(run_dir)
    print(json.dumps({"run": g["run"], "o1": g["o1"]["correct"], "n_defects": g["o2"]["n_defects"],
                      "fault_check": g["fault_check"]["ok"]}))
