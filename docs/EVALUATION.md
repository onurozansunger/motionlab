# Evaluation plan

Version 0.1 is a working application, not a completed research benchmark. No accuracy figure should be placed on a CV until the following evaluation has been run.

## Separate three questions

1. **Detection and tracking:** Are the boxes and object identities correct? Measure detection AP, IDF1 / HOTA, identity switches, and fragmentation on a dataset with compatible annotations.
2. **Moment retrieval:** Does a natural-language query retrieve its labeled time interval? Measure Recall@K at temporal IoU thresholds, plus negative-query behavior.
3. **Object attributes:** Are the color filters correct? Manually annotate a fixed development/test subset and report per-color precision and recall, with an explicit ambiguous category.

Keep whole videos and near-duplicate scenes in one split. Choose sampling rates, thresholds, and models on development data; freeze them before final test evaluation. Report unknown or unsupported categories separately. Do not interpret detector confidence or cosine similarity as calibrated accuracy.

## Ablations

- YOLOX-Tiny versus YOLOX-S with identical inputs.
- Detection-only fragments versus ByteTrack associations.
- Full-frame semantic retrieval versus object-crop retrieval.
- 1 FPS versus denser semantic sampling: retrieval quality, indexing time, memory.
- Simple crop colors versus a segmented foreground or learned attribute head.

## Planned extensions

Image-example search, annotated temporal retrieval, scene-aware event detection, and a reproducible benchmark report. Plate OCR and vehicle make/model estimation are separate optional tasks, not existing functionality.
