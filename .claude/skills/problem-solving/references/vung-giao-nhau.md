# Vùng giao nhau

Ép hai ý tưởng không liên quan chạm nhau để tìm tính chất mới.

Cách làm: lấy bài toán («đồng bộ phiên đăng nhập giữa các module web»),
lấy một hệ thống xa lạ («git đồng bộ nhánh»), rồi hỏi: «nếu coi mỗi phiên như một
nhánh git thì sao» — thao tác nào của git ánh xạ được sang đây (merge? rebase? stash?).

Quy tắc:

- Mượn **cơ chế**, không mượn từ ngữ. Không mang tên hệ thống kia vào mã của kho.
- Mỗi phép ẩn dụ chỉ được phép gợi ra tối đa một ý để thử; còn lại là trang trí.
- Ý nào sống sót thì viết lại bằng ngôn ngữ của kho (module, phiên, tuyến, khoá),
  rồi cho qua skill `debugging` kiểm chứng.

Dùng khi các cách thông thường đều không vừa — không phải kỹ thuật mặc định.
