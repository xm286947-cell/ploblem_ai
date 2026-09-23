# 常见问题

## REPORT_NOT_FOUND

Excel填写了报告名，但PDF目录没有匹配文件。查看`unmatched_reports.json`。

## AMBIGUOUS

存在多个同名或同ITR候选文件。查看`duplicate_report_matches.json`。

## SCANNED_PDF_SUSPECTED

PDF可能是扫描件。当前版本不执行OCR，需要先将扫描件转为可搜索PDF。

## Excel列名不同

修改`config/field_mapping.yaml`，不需要改代码。

## 能否直接使用其他目录

可以，通过`--excel`和`--reports-dir`传入。
