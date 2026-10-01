# Batch results -- harness 2.4.0

| model | heal | hyg | C1 | C2 | C3 | C4 | C5 | C6 | score | max | pct | passed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| solution | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 8.000 | 8.0 | 100.0% | True |
| adversarial_widened_15mm_clusters_at_original_spacing | 1.00 | 1.00 | 0.47 | 0.00 | 0.00 | 0.00 | 1.00 | 1.00 | 3.211 | 8.0 | 40.1% | False |
| adversarial_unwidened_shell_with_correct_clusters | 0.09 | 0.60 | 0.01 | 0.00 | 0.00 | 0.03 | 0.07 | 0.00 | 0.457 | 8.0 | 5.7% | False |
| adversarial_widened_by_30mm | 1.00 | 1.00 | 0.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 6.500 | 8.0 | 81.2% | False |
| adversarial_text_mirrored_incorrectly | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.15 | 1.00 | 1.00 | 6.300 | 8.0 | 78.8% | False |
| adversarial_only_one_button_cluster_mirrored | 1.00 | 1.00 | 1.00 | 0.38 | 1.00 | 0.50 | 1.00 | 1.00 | 6.062 | 8.0 | 75.8% | False |
| adversarial_feature_tree_with_errors | 0.61 | 0.80 | 0.37 | 0.19 | 0.00 | 0.30 | 0.61 | 0.61 | 3.064 | 8.0 | 38.3% | False |
| input | 1.00 | 1.00 | 0.00 | 0.00 | 1.00 | 0.00 | 1.00 | 1.00 | 3.000 | 8.0 | 37.5% | False |
| adversarial_missing_glyphs | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.00 | 7.000 | 8.0 | 87.5% | False |
| adversarial_unrequested_change_elsewhere | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.47 | 1.00 | 7.733 | 8.0 | 96.7% | False |
