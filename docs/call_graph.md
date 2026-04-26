# 專案函式總覽

> 共 9 個 Python 檔案，94 個函式（含私有函式）

---

## slot_evaluate.py — 老虎機盤面評分

| Function | 功能說明 | 被誰呼叫 |
|----------|----------|----------|
| `get_profile(pid)` | 依遊戲 ID 查詢遊戲配置字典，返回完整配置或 None | `get_board_key`、`get_mode`、`get_grid`（間接） |
| `get_board_key(pid)` | 提取遊戲配置中的 board_key，用於定位盤面資料位置，預設值 `"4"` | `wss_runtime.py` → `_maybe_evaluate_slot_spin` |
| `get_mode(pid)` | 提取遊戲配置中的模式（`payline` 或 `cascading`），預設 `"payline"` | `wss_runtime.py` → `_maybe_evaluate_slot_spin` |
| `get_grid(pid)` | 提取遊戲配置中的盤面尺寸 (rows, cols)，預設 `(5, 5)` | `wss_runtime.py` → `_maybe_evaluate_slot_spin` |
| `infer_grid_from_cells(cells)` | 從盤面 cells 的 position 欄位推導實際盤面尺寸 | `wss_runtime.py` → `_maybe_evaluate_slot_spin` |
| `_map_symbol(profile, code)` | 依 profile 的 symbol_map 將符號代碼轉換為符號名稱 | `decode_cells_to_reels` |
| `_resolve_wild(profile)` | 從 symbol_map 推導 wild 符號，用於 payline 計算 | `evaluate_line` |
| `_resolve_scatter(profile)` | 從 symbol_map 推導 scatter 符號 | `evaluate_line`、`evaluate_scatter` |
| `_resolve_count_on_board_payout(profile, count)` | 依 count_on_board 規則的區間 paytable 取得賠付金額 | `_resolve_cascading_payout` |
| `_resolve_cascading_payout(profile, symbol, count)` | 依 profile 的 win_rule 解析 cascading 模式下的賠付 | `evaluate_cascading_spin` |
| `decode_cells_to_reels(cells, profile)` | 將盤面 cells 轉為 reels 格式（外層欄、內層列） | `evaluate_spin_from_cells` |
| `evaluate_line(reels, payline, paytable, wild, scatter)` | 計算單條 payline 是否中獎，含 wild 匹配，回傳 (符號, 連數, 賠付, 路徑) | `evaluate_spin` |
| `evaluate_scatter(reels, scatter)` | 計算盤面上 scatter 符號的總數 | `evaluate_spin` |
| `evaluate_spin(reels, profile)` | Payline 模式整體評分，計算所有 payline 中獎 + scatter，回傳結果字典 | `evaluate_payline_spin` |
| `evaluate_payline_spin(reels, profile)` | Payline 模式入口，轉呼叫 `evaluate_spin` | `evaluate_spin_from_cells` |
| `evaluate_cascading_spin(reels, profile)` | Cascading 模式入口，支援 count_on_board 與 ways 兩種規則 | `evaluate_spin_from_cells` |
| `evaluate_spin_from_cells(cells, pid)` | 盤面評分主入口，依遊戲模式分流至 cascading 或 payline 評分 | `wss_runtime.py` → `_maybe_evaluate_slot_spin` |
| `find_win_ways_max(reels, symbol, wild)` | 找出指定符號的最大連續中獎 ways，迭代乘積演算法 | `evaluate_cascading_spin` |

---

## slot_profiles.py — 遊戲配置與 Protobuf 模式定義

| Function | 功能說明 | 被誰呼叫 |
|----------|----------|----------|
| `_load_game_profiles_raw()` | 讀取 YAML 配置檔案，失敗時提供詳細錯誤訊息 | `_load_slot_profiles` |
| `_normalize_paytable(paytable)` | 將 YAML 內的字串數字 key 轉回 Python int，方便查表 | `_normalize_profile` |
| `_normalize_profile(profile)` | 正規化遊戲配置：轉換 grid 為 tuple、展開 paylines_ref、正規化 paytable 與 symbol_map | `_load_slot_profiles` |
| `_load_slot_profiles()` | 載入全部 YAML 配置後逐個正規化，輸出 runtime 共用設定字典 | 模組全域初始化 |

---

## wss_runtime.py — WebSocket 連線與遊戲迴圈

| Function | 功能說明 | 被誰呼叫 |
|----------|----------|----------|
| `_is_numeric_string(s)` | 判斷字串是否為純數字格式（含浮點），使用正規表達式 | `run_single_attempt` |
| `_log_missing_symbol_to_csv(...)` | 遇到 symbol_map 缺少 key 時，將記錄寫入 missing_symbols.csv，含鎖防並發 | `_maybe_evaluate_slot_spin` |
| `_resolve_profile_value(profile, key, events)` | 依 profile 定義的 source / fallback / default 規則，從 events.loop 取值 | `_build_slot_loop_payload` |
| `_build_slot_loop_payload(profile, events)` | 依 profile 的 payload 設定，組出 slot client:spin 要送的資料，支援多種編碼器 | `run_single_attempt` |
| `call_with_log(sio, event, data, ...)` | 發送 socket 事件並等待 ACK，記錄日誌、解碼 protobuf、執行盤面評分 | `run_single_attempt`（多處） |
| `create_socket_client(config, ...)` | 建立 socket client 並註冊事件處理（connect、disconnect、catch_all） | `run_single_attempt` |
| `_maybe_evaluate_slot_spin(payload, pid, ...)` | 若 payload 具備盤面資料，執行評分並記錄到日誌 | `call_with_log` |
| `run_single_attempt(config, loginname, ...)` | 單次連線流程：verify API 取 token → socket 連接 → 送 init → 主迴圈 spin | `run_account_main`、`test_locustfile.py`、`test_wss_tool.py` |
| `run_account_main(config, loginname, ...)` | 單帳號主流程，含重試邏輯（exponential backoff），初始化 logger 與 csv_writer | `test_locustfile.py`、`test_wss_tool.py` |

---

## wss_helpers.py — 工具函式與 Protobuf 編解碼

| Function | 功能說明 | 被誰呼叫 |
|----------|----------|----------|
| `_decode_varint(data, pos)` | 解碼 protobuf varint，回傳值與新索引 | `decode_fish_spawn_id`、`_decode_packed_varint` |
| `_encode_varint(value)` | 將非負整數編碼為 protobuf varint | `encode_spin_amount_payload_ss01/02/03`、`encode_bullet_hit_payload` |
| `_deep_merge_dict(base, override)` | 遞迴合併 dict，讓 env.json 的設定覆蓋 game profile 預設值 | `load_env` |
| `load_env(env_path)` | 讀取 env.json，驗證必要欄位，與 slot_profiles 合併成 runtime config | `test_wss_tool.py`、`test_locustfile.py` |
| `build_run_dir(config)` | 依時間建立執行目錄（yyyy/mm/dd/pid_hhmmss 格式） | `test_wss_tool.py`、`test_locustfile.py` |
| `build_account_logger(config, loginname, run_dir)` | 為單一帳號建立 loguru logger 與 sink，設定檔案輪轉與格式 | `wss_runtime.py` → `run_account_main` |
| `_parse_rotation_size(size_str)` | 將類似 `'100 MB'`、`'1 GB'` 的字串轉換為 bytes | `build_account_csv_writer` |
| `build_account_csv_writer(config, loginname, run_dir)` | 建立 CSV 寫入 callable，支援檔案輪轉 | `wss_runtime.py` → `run_account_main` |
| `build_payload(config, loginname)` | 把 loginname 套進 verify payload template，組出登入驗證用 JSON | `wss_runtime.py` → `run_single_attempt` |
| `decode_trigger_payload_to_tokens(payload)` | 解析 free spin 觸發 payload，抽出後續 client:free_spin 需要的 token | `wss_runtime.py` → `run_single_attempt` |
| `encode_free_spin_token_as_protobuf(token)` | 將 free spin token 以 protobuf 格式封裝後再 base64 | `wss_runtime.py` → `run_single_attempt` |
| `encode_spin_amount_payload_ss01(bet_amount)` | SS01 的 spin request 編碼，僅 bet_amount 欄位 | `_SPIN_PAYLOAD_ENCODERS`（`wss_runtime.py`） |
| `encode_spin_amount_payload_ss02(bet_amount, ...)` | 將 SS02 的下注資料封裝成巢狀 protobuf，再轉成 base64 | `_SPIN_PAYLOAD_ENCODERS`（`wss_runtime.py`） |
| `encode_spin_amount_payload_ss03(bet_amount, ...)` | 將 SS03 的下注資料封裝成巢狀 protobuf（格式同 SS02） | `_SPIN_PAYLOAD_ENCODERS`（`wss_runtime.py`） |
| `decode_fish_spawn_id(payload)` | 解出魚 spawn payload 中的 fish_id，手動 varint 解碼 | `wss_runtime.py` → `run_single_attempt` |
| `_normalize_non_negative_int(value, name)` | 驗證並正規化為非負整數 | `encode_spin_amount_payload_ss02/03`、`encode_bullet_hit_payload` |
| `encode_bullet_hit_payload(fish_id, bullet_id, bet)` | 組出魚機子彈命中 payload（base64） | `wss_runtime.py` → `run_single_attempt` |
| `extract_trigger_payload(args)` | 從 socketio 回呼參數中找出第一個字串 payload，廣度優先搜尋 | `wss_runtime.py` → `run_single_attempt`（多處） |
| `bytes_to_readable(value)` | 將 bytes 轉為可讀字串或 hex，遞迴處理 dict/list | `decode_protobuf_to_json` |
| `_decode_packed_varint(raw_bytes)` | 解碼 packed varint，修復被 blackboxprotobuf 誤判的情況 | `_apply_protobuf_schema` |
| `_decode_double(raw_int)` | 將被誤解為整數的 64-bit 數值還原為 float（double） | `_apply_protobuf_schema` |
| `_normalize_grid_cell_defaults(obj, schema)` | 在 grid cell schema 下補齊缺失的 code 預設值 | `_apply_protobuf_schema` |
| `_apply_protobuf_schema(obj, schema, ...)` | 將 blackboxprotobuf 盲解結果轉成可讀欄位名稱，遞迴處理 | `decode_protobuf_to_json`、自身遞迴 |
| `decode_protobuf_to_json(b64_data, pid, event)` | 將 base64 protobuf 轉成 JSON 字串，套用遊戲專用 schema | `wss_runtime.py` → `call_with_log`（多處） |
| `_flatten_dict(d, prefix)` | 將巢狀 dict 展平成單層欄位，列表轉成 JSON 字串 | `write_csv_log` |
| `write_csv_log(csv_writer, event, decoded, ...)` | 將 decoded 資料寫入 CSV 檔，支援 summary 與一般事件兩種格式，含輪轉 | `build_account_csv_writer` → `_write` |

---

## test_wss_tool.py — 主程式與批次執行

| Function | 功能說明 | 被誰呼叫 |
|----------|----------|----------|
| `main(config, loginname, run_dir)` | 單帳號主流程包裝，轉呼叫 `run_account_main` | `limited_worker` |
| `run_all(config, loginnames, run_dir)` | 批次並發控制：Semaphore 限流 + asyncio.gather + 排序 total_summary.csv | 模組主程式（`__main__`） |
| `limited_worker(loginname)` | Semaphore 內呼叫 `main` 以實現並發控制（`run_all` 內的嵌套函式） | `run_all`（建立 task 時） |
| `sort_key(loginname)` | 帳號尾碼有數字時用數字排序，否則退回字串排序，用於 total_summary.csv | `run_all` → `sorted()` |

---

## test_locustfile.py — Locust 負載測試

| Function | 功能說明 | 被誰呼叫 |
|----------|----------|----------|
| `next_loginname()` | 循環取得下一個登入帳號名稱，用鎖保護全域計數器 | `WssToolUser.on_start` |
| `WssToolUser.on_start(self)` | Locust User 初始化，為每個虛擬使用者分配帳號、日誌等級、執行目錄 | Locust 框架自動呼叫 |
| `WssToolUser.run_wss_flow(self)` | Locust User 任務，執行一次完整遊戲流程並記錄時間、異常（`@task`） | Locust 框架自動呼叫 |

---

## scripts/generate_slot_profiles.py — Protobuf 模式生成工具

| Function | 功能說明 | 被誰呼叫 |
|----------|----------|----------|
| `_strip_comments(code)` | 移除 C++ 程式碼中的 `//` 單行註解 | `_parse_proto_messages` |
| `_parse_proto_messages(cpp_files)` | 用正規表達式解析 .cpp protobuf 定義，抽出 message 與 field 資訊 | `generate_schema_block` |
| `_python_repr(value)` | 將 Python 值轉成可用於程式碼生成的字串表示 | `_field_mapping`、`_render_schema` |
| `_shared_alias_for_type(type_name)` | 查詢 shared 類型的 alias 對應表 | `_field_mapping` |
| `_field_mapping(field)` | 將 protobuf field 轉成 Python 欄位映射（tuple 或字串） | `_build_schema` |
| `_build_schema(fields)` | 將 field list 轉成 `{field_num: mapping}` 的字典 | `generate_schema_block` |
| `_render_schema(schema)` | 將 schema 字典轉成 Python 程式碼字串表示 | `generate_schema_block` |
| `generate_schema_block(proto_dirs)` | 解析所有 protobuf 檔案、生成 schema block 字串（含 marker） | `write_slot_profiles` |
| `write_slot_profiles(output_path, block)` | 將生成的 schema block 寫回 slot_profiles.py，替換 marker 區間內容 | 模組主程式（`__main__`） |

---

## event/logger.py — 事件日誌模組

| Function | 功能說明 | 被誰呼叫 |
|----------|----------|----------|
| `_build_log_path(config, timestamp)` | 根據 config 和時間戳生成日誌檔案路徑 | `init_logger` |
| `_get_or_create_default_logger()` | 取得或建立全域預設 logger 實例，初次呼叫時初始化 | `log_debug`、`log_info`、`log_warning`、`log_error`、`log_exception` |
| `init_logger(config, name, ...)` | 初始化命名 logger，設定 level、handler、formatter，支援 RotatingFileHandler | 未被專案內部呼叫（供外部使用） |
| `log_debug(msg, ...)` | 記錄 DEBUG 級別日誌 | 未被專案內部呼叫 |
| `log_info(msg, ...)` | 記錄 INFO 級別日誌 | 未被專案內部呼叫 |
| `log_warning(msg, ...)` | 記錄 WARNING 級別日誌 | 未被專案內部呼叫 |
| `log_error(msg, ...)` | 記錄 ERROR 級別日誌 | 未被專案內部呼叫 |
| `log_exception(msg, ...)` | 記錄 EXCEPTION 級別日誌（含 traceback） | 未被專案內部呼叫 |
