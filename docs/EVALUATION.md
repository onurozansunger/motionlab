# Evaluation plan

Version 0.2 is a working application, not a completed research benchmark. No accuracy figure should be placed on a CV until the following evaluation has been run.

## Separate four questions

1. **Detection and tracking:** Are the boxes and object identities correct? Measure detection AP, IDF1 / HOTA, identity switches, and fragmentation on a dataset with compatible annotations.
2. **Moment retrieval:** Does a natural-language query retrieve its labeled time interval? Measure Recall@K at temporal IoU thresholds, plus negative-query behavior.
3. **Object attributes:** Are the color filters correct? Manually annotate a fixed development/test subset and report per-color precision and recall, with an explicit ambiguous category.

4. **Appearance retrieval:** Does an example crop retrieve visually relevant tracks in other clips? Annotate relevance independently of identity, exclude duplicate source footage, and measure Recall@K / mAP with single-crop and multi-crop baselines.

Keep whole videos and near-duplicate scenes in one split. Choose sampling rates, thresholds, and models on development data; freeze them before final test evaluation. Report unknown or unsupported categories separately. Do not interpret detector confidence or cosine similarity as calibrated accuracy.

## Ablations

- YOLOX-Tiny versus YOLOX-S with identical inputs.
- Detection-only fragments versus ByteTrack associations.
- Full-frame semantic retrieval versus object-crop retrieval.
- 1 FPS versus denser semantic sampling: retrieval quality, indexing time, memory.
- Simple crop colors versus a segmented foreground or learned attribute head.

## Planned extensions

Annotated temporal retrieval, scene-aware event detection, external image-example uploads, and a reproducible benchmark report. Track-example appearance search is implemented; its relevance has not yet been benchmarked. Plate OCR and vehicle make/model estimation are separate optional tasks, not existing functionality.
