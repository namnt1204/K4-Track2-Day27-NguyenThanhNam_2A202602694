# Data Siege — Engineering Reflection

## 1. Cơ chế hoạt động của Data Siege
Data Siege mô phỏng một môi trường phòng thủ Data Observability trong thời gian thực: một luồng tuần tự (event stream) gồm các sự kiện pipeline (`data_batch`, `contract_checkpoint`, `lineage_run`, `feature_materialization`, `embedding_batch`) đi qua `solution/defense.py`. Hệ thống phải đưa ra quyết định độc lập trên từng sự kiện (`alert` hoặc `stay quiet`) mà không biết trước nhãn, không có file I/O, và mọi truy vấn chẩn đoán chuyên sâu (`ctx.tools`) đều bị trừ điểm tín chỉ (compute budget) có giới hạn.

## 2. Loại lỗi khó phát hiện nhất và nguyên nhân
Lỗi khó phát hiện nhất là **Subtle Feature Skew / Semantic Drift** trong `ai_infra` và các lỗi **Lineage đứt gãy phi đối xứng** (`orphan_output` trên intermediate models):
- **Feature & Embedding Drift tinh vi**: Các giá trị độ lệch trung bình (mean) hoặc centroid shift nằm sát ngưỡng 2.5 sigma - 3.0 sigma. Nếu đặt ngưỡng cứng quá thấp, FPR sẽ tăng do nhiễu tự nhiên; nếu đặt quá cao theo static 3 sigma, các lỗi tinh vi trong Private phase sẽ bị lọt lưới.
- **Lineage anomalies**: Cấu trúc đồ thị DAG không thể suy luận chỉ qua một ngưỡng số học. Việc thiếu node phụ thuộc hoặc output mồ côi đòi hỏi phải phân tích đúng topology và vai trò của từng model (staging vs mart/reporting).

## 3. Kết hợp Single-signal và Multi-signal Detection
- **Single-signal**: Bắt giữ ngay lập tức các vi phạm nghiêm trọng vượt ngưỡng 3-sigma (như `null_rate > 0.0109`, `staleness_min > 8.418m`, `duration_ms > 5134.98ms`, hoặc contract có violations).
- **Multi-signal (Composite Anomaly)**: Chuẩn hóa khoảng cách độ lệch trên từng chiều:
  `dev = abs(metric - center) / half_range`
  Thuật toán yêu cầu đồng thời hai điều kiện: ít nhất 2 chiều có `dev >= 0.80` VÀ chuẩn tổng bình phương (squared L2 norm) `sum(dev_i^2) >= 1.40`. Việc kết hợp đồng thời cả hai điều kiện ngăn chặn báo động sai khi chỉ một chiều biến động mạnh hoặc hai chiều mới chỉ chớm chạm 0.80 (vì `0.80^2 + 0.80^2 = 1.28 < 1.40`).
- **Thực nghiệm FPR**: FPR thực nghiệm đạt 0.0% trên cả Practice, Public và Private. Tuy nhiên, adversarial stress-test cho thấy hệ thống vẫn có thể kích hoạt cảnh báo ở vùng biên khi nhiều chiều đồng thời dao động nhân tạo gần ngưỡng.

## 4. Đánh đổi giữa TPR, FPR và Chi phí Tool
Công thức tính điểm:
`Score = 100 * (0.5 * TPR - 0.3 * FPR - 0.2 * min(cost_overage, 1))`
- **Trọng số phạt nặng của FPR (-0.3)**: 1 lần báo động sai làm mất điểm tương đương với việc bắt được 0.6 lỗi thật. Do đó, kiểm soát FPR = 0.0% là ưu tiên hàng đầu.
- **Tradeoff chi phí**: Mỗi RPC tiêu tốn từ 1.0 đến 2.0 credits. Trên tập Public (160 events), việc gọi 1 RPC/event dẫn đến spend = 240.0 so với budget 220.0 (overage = 0.0909), bị trừ phạt 1.82 điểm. Tuy nhiên, nếu bỏ qua RPC để tiết kiệm chi phí sẽ gây mất 1.28 điểm cho mỗi lỗi bỏ sót. Chỉ cần bỏ sót 2 lỗi là thiệt hại lớn hơn toàn bộ tiền phạt vượt ngân sách. Chiến lược "tối đa 1 RPC/event, không gọi lặp" là điểm cân bằng tối ưu.

## 5. Kết quả thực nghiệm qua các Phase
- **Practice Phase**: Score = **50.00 / 50.0** | TPR = **100.0%** (33/33) | FPR = **0.0%** (0/87) | Spend = **180.0 / 220.0** | 4/4 Pillars: **High**.
- **Public Phase**: Score = **46.90 / 50.0** | TPR = **97.44%** (38/39) | FPR = **0.0%** (0/121) | Spend = **240.0 / 220.0** (Overage = 0.0909) | 4/4 Pillars: **High**.
- **Private Phase**: Score = **32.41** | TPR = **64.81%** | FPR = **0.0%** | Cost Overage = **0.0000** | HMAC Verified.

## 6. Hướng cải tiến với ctx.state
1. **Exponentially Weighted Moving Average (EWMA)**: Theo dõi xu hướng trôi dạt của `mean_amount` và `row_count` theo thời gian thực thay vì giả định phân phối tĩnh.
2. **CUSUM (Cumulative Sum Control Chart)**: Tích lũy độ lệch nhỏ liên tiếp để phát hiện step-change và trend drift nhanh chóng với độ trễ tối thiểu.
3. **Adaptive Budgeting**: Khi `ctx.tools.budget_remaining()` giảm dưới ngưỡng an toàn, áp dụng heuristic gating dựa trên payload metadata trước khi quyết định gọi các tool đắt đỏ (2.0 credits).

## 7. Kiểm soát Overfitting
- Tuyệt đối không hardcode seed, ID hoặc quy tắc riêng lẻ của từng run.
- Ngưỡng được neo chặt theo các tham số chuẩn hóa từ `data/baselines.json` (mean ± 3 sigma).
- Đảm bảo thuật toán duy trì tính tổng quát hóa cao trên stream dữ liệu hoàn toàn chưa biết ở phase Private.
