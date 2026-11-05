# 藥你安心：藥事安全Agent

整合藥事 Agent 仿單與 MedHop 研究資料，提供病歷管理、藥物資料檢索及用藥風險提醒。

## 主要功能

- 藥師 Agent：每則對話訊息交給模型，由模型呼叫工具建立或查詢病歷、更新病史與用藥、試算併用、執行完整檢查、查仿單與暫存藥品資料；不再用關鍵字規則判斷輸入。
- 查詢或新增病歷，持續更新用藥紀錄。
- 檢索藥品成分、仿單與交互作用相關證據。
- 顯示禁忌與併用風險提醒、MedHop 研究路徑。
- 保存檢查結果、藥師處置與用藥說明。
- 在右側病歷直接編輯基本資料、病史、過敏史與用藥，並以 JSON 匯出或匯入個案。
- 藥物說明連到食藥署仿單、UpToDate、Micromedex 與 OpenEvidence。
- 藥師帳號登入（含「Demo 體驗」一鍵進入）與模擬訂閱方案；異動紀錄記錄登入的藥師。
- 藥品資料也在對話中處理：依藥師提供的許可證字號取得食藥署電子仿單、列出候選產品讓藥師選擇，或暫存藥師貼上的內容；藥師確認後才寫入藥品資料庫並加入檢索。

## 主要檔案

| 檔案/資料夾 | 用途 |
|---|---|
| `static/` | 網頁介面 |
| `asgi.py` | 後端 API |
| `pharmacist_agent.py` | 藥師 Agent：工具定義、執行迴圈與工具端防護 |
| `workflow.py` | 病歷與用藥處理 |
| `record_tools.py` | 病歷 JSON 匯入匯出與藥師編輯 |
| `account.py` | 藥師登入與模擬訂閱 |
| `drug_knowledge.py` | 食藥署電子仿單、藥師貼上的藥品資料與確認寫入 |
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
改用其他 OpenAI 相容端點時設定環境變數 `MODEL_URL`、`MODEL`，需要金鑰時另設 `MODEL_API_KEY`（不要寫進檔案或提交到 repo）。

對話由藥師 Agent 處理，模型必須支援 OpenAI 相容的 tool calling（`tools`、`tool_choice`）。模型無法連線時，對話會顯示錯誤，不會改用規則猜測；右側病歷編輯與完整檢查本身不依賴模型。工具與防護說明見 `INTEGRATION-GUIDE.md` 的「藥師 Agent：對話輸入」。


## 資料來源

- **MedHop**：包含 1,000 份文件、1,129 個文字單元、4,385 個實體及 6,082 條關係，用於研究資料與圖譜路徑檢索。
- **藥事 Agent**：使用 moda-agent 的 100 份原始藥品 PDF，共 187 頁可檢索，其中 65 頁透過 OCR 辨識；另有 4 頁文字不足，無法完整檢索。
- **食藥署電子仿單**：藥師確認後由 `mcp.fda.gov.tw` 的 GetDrugDoc 取得，保留版本與核准日。許可證代碼對照與解析方式參考 [shin13/opentaimed](https://github.com/shin13/opentaimed)（MIT，授權文字見 `licenses/opentaimed-LICENSE`）。


