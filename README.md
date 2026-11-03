# 藥你安心：藥事安全Agent

整合藥事 Agent 仿單與 MedHop 研究資料，提供病歷管理、藥物資料檢索及用藥風險提醒。

## 主要功能

- 對話輸入病人資料、病史與用藥。
- 查詢或新增病歷，持續更新用藥紀錄。
- 檢索藥品成分、仿單與交互作用相關證據。
- 顯示禁忌與併用風險提醒、MedHop 研究路徑。
- 保存檢查結果、藥師處置與用藥說明。
- 在右側病歷直接編輯基本資料、病史、過敏史與用藥，並以 JSON 匯出或匯入個案。
- 藥物說明連到食藥署仿單、UpToDate、Micromedex 與 OpenEvidence。

## 主要檔案

| 檔案/資料夾 | 用途 |
|---|---|
| `static/` | 網頁介面 |
| `asgi.py` | 後端 API |
| `workflow.py` | 病歷與用藥處理 |
| `record_tools.py` | 病歷 JSON 匯入匯出與藥師編輯 |
| `pharmacy_rag.py` | 仿單 RAG 檢索 |
| `label_interactions.py` | 已核驗的併用警示 |
| `population_checks.py` | 病人條件與禁忌檢查 |
| `pair_integration.py` | 藥物配對與 MedHop 路徑整合 |
| `data/` | 藥品及研究資料 |
| `validation/` | 測試程式與驗證紀錄 |

## 本機網頁

http://127.0.0.1:8502

## 本機模型

預設模型：`qwen3-it-4b-FLM`  
主機 API：`http://127.0.0.1:13305/api/v1`


## 資料來源

- **MedHop**：包含 1,000 份文件、1,129 個文字單元、4,385 個實體及 6,082 條關係，用於研究資料與圖譜路徑檢索。
- **藥事 Agent**：使用 moda-agent 的 100 份原始藥品 PDF，共 187 頁可檢索，其中 65 頁透過 OCR 辨識；另有 4 頁文字不足，無法完整檢索。


