# Cú pháp bốn loại sơ đồ

## flowchart

- Hướng: `TD` (trên-xuống), `LR` (trái-phải).
- Nút: `[chữ nhật]`, `{quyết định}`, `([bo tròn])`, `[[tác vụ]]`.
- Mũi tên có nhãn: `A -->|nhãn| B`. Chú thích: `%% dòng chú thích`.

## sequenceDiagram

- Khai báo bên tham gia: `participant L as Lõi web2api` (tên hiển thị sau `as`).
- Gọi đồng bộ: `A->>B: nội dung`; trả về: `B-->>A: nội dung`.
- Khối: `alt ... else ... end`, `loop ... end`, `opt ... end`.
- Ghi chú: `Note over A,B: nội dung`.

## stateDiagram-v2

- Trạng thái bắt đầu/kết thúc: `[*]`.
- Chuyển có nhãn: `A --> B: điều kiện`.
- Trạng thái gộp: `state "Tên hiển thị" as id`.

## erDiagram

- Quan hệ: `||--||` (một-một), `||--o{` (một-nhiều), `}o--o{` (nhiều-nhiều).
- Thuộc tính:
  ```
  erDiagram
      TAI-KHOAN {
          string id PK
          string ten
      }
  ```

Mẹo chung: tên bên tham gia/bảng dùng tiếng Việt không dấu hoặc gạch nối để tránh lỗi font.
