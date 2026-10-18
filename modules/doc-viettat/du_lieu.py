"""Dữ liệu tập đóng cho việc chuẩn hoá chữ đọc tiếng Việt.

GIẤY PHÉP VÀ NGUỒN GỐC DỮ LIỆU:
- Bảng từ viết tắt một nghĩa, đơn vị đo, tiền tệ và bảng tuần hoàn nguyên tố trong tệp này
  được tổng hợp và biên soạn độc lập bởi nhóm phát triển web2api theo giấy phép CC0 1.0 Universal
  (Public Domain Dedication). Không sao chép nguyên khối từ điển của các dự án bên ngoài.
- Tên các nguyên tố hoá học tuân theo danh pháp tiếng Việt tiêu chuẩn của Bộ Giáo dục & Đào tạo.
- Bảng đơn vị đo tuân theo hệ đo lường quốc tế (SI) và các đơn vị thực tế thông dụng tại Việt Nam.
"""

from __future__ import annotations

# Tên chữ cái tiếng Việt (tập đóng chuẩn để đánh vần viết tắt)
TEN_CHU: dict[str, str] = {
    "A": "a", "B": "bê", "C": "xê", "D": "đê", "Đ": "đê", "E": "e", "F": "ép", "G": "gờ",
    "H": "hát", "I": "i", "J": "giây", "K": "ca", "L": "lờ", "M": "mờ", "N": "nờ", "O": "ô",
    "P": "phê", "Q": "qui", "R": "rờ", "S": "ét", "T": "tê", "U": "u", "V": "vê",
    "W": "vê kép", "X": "ích", "Y": "i", "Z": "dét",
}

# Số La Mã từ 1 đến 39 (I đến XXXIX)
_LA_MA_DON_VI = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9}


def chuyen_so_la_ma(s: str) -> int | None:
    """Chuyển chuỗi số La Mã hợp lệ (1-39) thành số nguyên, None nếu không hợp lệ."""
    import re
    m = re.fullmatch(r"(X{0,3})(IX|IV|VIII|VII|VI|V|III|II|I)?", s)
    if not m or not s:
        return None
    chuc = len(m.group(1)) * 10
    dv = _LA_MA_DON_VI.get(m.group(2) or "", 0)
    tong = chuc + dv
    return tong if tong > 0 else None


# Đơn vị đo SI và thông dụng (tập đóng)
# Sắp xếp theo độ dài giảm dần khi dùng regex
DON_VI: dict[str, str] = {
    "km/h": "ki lô mét trên giờ",
    "m/s": "mét trên giây",
    "kWh": "ki lô oát giờ",
    "kwh": "ki lô oát giờ",
    "Wh": "oát giờ",
    "kcal": "ki lô ca lo",
    "cal": "ca lo",
    "mAh": "mi li am pe giờ",
    "Ah": "am pe giờ",
    "Gbps": "gi ga bít trên giây",
    "Mbps": "mê ga bít trên giây",
    "kbps": "ki lô bít trên giây",
    "GHz": "gi ga héc",
    "MHz": "mê ga héc",
    "kHz": "ki lô héc",
    "Hz": "héc",
    "kW": "ki lô oát",
    "MW": "mê ga oát",
    "W": "oát",
    "V": "vôn",
    "mV": "mi li vôn",
    "mA": "mi li am pe",
    "A": "am pe",
    "Nm": "niu tơn mét",
    "N": "niu tơn",
    "km": "ki lô mét",
    "cm": "xen ti mét",
    "mm": "mi li mét",
    "nm": "na nô mét",
    "m": "mét",
    "kg": "ki lô gam",
    "mg": "mi li gam",
    "g": "gam",
    "ml": "mi li lít",
    "lít": "lít",
    "lit": "lít",
    "l": "lít",
    "ph": "phút",
    "h": "giờ",
    "s": "giây",
    "°C": "độ C",
    "%": "phần trăm",
    "K": "ken vin",
    "mmol/L": "mi li mol trên lít",
    "mol/L": "mol trên lít",
    "mmol": "mi li mol",
    "mol": "mol",
    "TB": "tê ra bai",
    "GB": "gi ga bai",
    "MB": "mê ga bai",
    "KB": "ki lô bai",
    "ha": "héc ta",
}

# Tiền tệ (mã ISO 4217 thông dụng + tiền số lớn)
TIEN_TE: dict[str, str] = {
    "VND": "đồng",
    "VNĐ": "đồng",
    "đ": "đồng",
    "USD": "đô la Mỹ",
    "EUR": "ơ rô",
    "JPY": "yên Nhật",
    "CNY": "nhân dân tệ",
    "GBP": "bảng Anh",
    "KRW": "uôn Hàn Quốc",
    "SGD": "đô la Xin ga po",
    "THB": "bạt Thái",
    "AUD": "đô la Úc",
    "CAD": "đô la Ca na đa",
    "HKD": "đô la Hồng Kông",
    "TWD": "đài tệ",
    "CHF": "phrăng Thụy Sĩ",
    "INR": "ru pi Ấn Độ",
    "RUB": "rúp Nga",
    "MYR": "rinh gít Ma lai xi a",
    "IDR": "ru pi a In đô nê xi a",
    "BTC": "bít côi",
    "ETH": "ê thơ ri um",
    "USDT": "u ét đê tê",
}

# 118 nguyên tố hoá học bảng tuần hoàn (tập đóng)
NGUYEN_TO: frozenset[str] = frozenset("""
H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu
Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba
La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi
Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds
Rg Cn Nh Fl Mc Lv Ts Og""".split())

# Tên nguyên tố hoá học tiếng Việt
TEN_NGUYEN_TO: dict[str, str] = {
    "H": "hi đrô", "He": "hê li", "Li": "li ti", "Be": "bê ri", "B": "bo", "C": "các bon",
    "N": "ni tơ", "O": "ô xi", "F": "phờ lo", "Ne": "nê ông", "Na": "nát tri", "Mg": "ma giê",
    "Al": "nhôm", "Si": "si líc", "P": "phốt pho", "S": "lưu huỳnh", "Cl": "cờ lo", "Ar": "a gông",
    "K": "ka li", "Ca": "can xi", "Sc": "xcan đi", "Ti": "ti tan", "V": "va na đi", "Cr": "crôm",
    "Mn": "man gan", "Fe": "sắt", "Co": "cô ban", "Ni": "ni ken", "Cu": "đồng", "Zn": "kẽm",
    "Ga": "ga li", "Ge": "giéc ma ni", "As": "a xen", "Se": "xê len", "Br": "brôm", "Kr": "kríp tôn",
    "Rb": "ru bi đi", "Sr": "xtrôn ti", "Y": "y tri", "Zr": "di cô ni", "Nb": "ni ô bi", "Mo": "mô líp đen",
    "Tc": "tếch nê ti", "Ru": "ru tê ni", "Rh": "rô đi", "Pd": "pa la đi", "Ag": "bạc", "Cd": "ca đi mi",
    "In": "in đi", "Sn": "thiếc", "Sb": "an ti moan", "Te": "tê lu", "I": "i ốt", "Xe": "xê nông",
    "Cs": "xê si", "Ba": "ba ri", "La": "lan tan", "Ce": "xê ri", "Pr": "pra xê ô đim", "Nd": "nê ô đim",
    "Pm": "prô mê ti", "Sm": "sa ma ri", "Eu": "ơ rô pi", "Gd": "ga đô li ni", "Tb": "téc bi", "Dy": "đi xprô xi",
    "Ho": "hôn mi", "Er": "éc bi", "Tm": "tu li", "Yb": "y téc bi", "Lu": "lu tê xi", "Hf": "háp ni",
    "Ta": "tan tan", "W": "vôn fram", "Re": "rê ni", "Os": "ô xmi", "Ir": "i ri đi", "Pt": "pla tin",
    "Au": "vàng", "Hg": "thủy ngân", "Tl": "ta li", "Pb": "chì", "Bi": "bít mút", "Po": "pô lô ni",
    "At": "a xta tin", "Rn": "ra đông", "Fr": "phran xi", "Ra": "ra đi", "Ac": "ác ti ni", "Th": "thô ri",
    "Pa": "prô tác ti ni", "U": "u ra ni", "Np": "nép tu ni", "Pu": "plu tô ni", "Am": "a mê ri xi",
    "Cm": "cu ri", "Bk": "béc kê li", "Cf": "ca li phoóc ni", "Es": "anh xtai ni", "Fm": "phéc mi",
    "Md": "men đê lê vi", "No": "nô bê li", "Lr": "lô ren xi", "Rf": "rơ dơ phoóc đi", "Db": "đúp ni",
    "Sg": "xi bo gi", "Bh": "bô ri", "Hs": "ha xi", "Mt": "mai nơ ri", "Ds": "đam xta đi", "Rg": "rơn ghen",
    "Cn": "cô péc ni xi", "Nh": "ni hô ni", "Fl": "phlê rô vi", "Mc": "mát cô vi", "Lv": "li vơ mo ri",
    "Ts": "ten nê xin", "Og": "ô ga nét xôn",
}

# Ký hiệu phản ứng hoá học
PHAN_UNG: dict[str, str] = {
    "→": "tạo thành",
    "⟶": "tạo thành",
    "⇌": "thuận nghịch",
    "↑": "bay lên",
    "↓": "kết tủa",
}

# Từ xưng hô tiếng Việt (tập đóng để nhận diện tên người: cô Na, anh Ba, chú Ba...)
XUNG_HO: frozenset[str] = frozenset("""
anh chị em cô chú bác dì cậu mợ thím dượng ông bà cụ thầy bạn bé con cháu
mẹ bố cha má u chồng vợ luật sư bác sĩ giáo viên tiến sĩ tác giả bệnh nhân""".split())

# Bảng từ viết tắt MỘT NGHĨA tự soạn (≥160 từ phổ biến, CC0)
VIET_TAT_MOT_NGHIA: dict[str, str] = {
    "UBND": "Ủy ban nhân dân",
    "HĐND": "Hội đồng nhân dân",
    "CSGT": "cảnh sát giao thông",
    "PCCC": "phòng cháy chữa cháy",
    "BHYT": "bảo hiểm y tế",
    "BHXH": "bảo hiểm xã hội",
    "CMND": "chứng minh nhân dân",
    "CCCD": "căn cước công dân",
    "THPT": "trung học phổ thông",
    "THCS": "trung học cơ sở",
    "ĐH": "đại học",
    "CĐ": "cao đẳng",
    "PGS": "phó giáo sư",
    "TS": "tiến sĩ",
    "ThS": "thạc sĩ",
    "BS": "bác sĩ",
    "GĐ": "giám đốc",
    "PGD": "phó giám đốc",
    "BQL": "ban quản lý",
    "DNNN": "doanh nghiệp nhà nước",
    "NĐ-CP": "nghị định Chính phủ",
    "QĐ-UBND": "quyết định Ủy ban nhân dân",
    "TT-BTC": "thông tư Bộ Tài chính",
    "TW": "trung ương",
    "BCH": "ban chấp hành",
    "KTX": "ký túc xá",
    "NXB": "nhà xuất bản",
    "VĐV": "vận động viên",
    "HLV": "huấn luyện viên",
    "HCV": "huy chương vàng",
    "HCB": "huy chương bạc",
    "HCĐ": "huy chương đồng",
    "CLB": "câu lạc bộ",
    "ĐTQG": "đội tuyển quốc gia",
    "ODA": "vốn hỗ trợ phát triển chính thức",
    "FDI": "đầu tư trực tiếp nước ngoài",
    "GDP": "tổng sản phẩm quốc nội",
    "CPI": "chỉ số giá tiêu dùng",
    "WTO": "tổ chức thương mại thế giới",
    "WHO": "tổ chức y tế thế giới",
    "WB": "ngân hàng thế giới",
    "IMF": "quỹ tiền tệ quốc tế",
    "ASEAN": "hiệp hội các quốc gia Đông Nam Á",
    "APEC": "diễn đàn hợp tác kinh tế châu Á Thái Bình Dương",
    "UNESCO": "tổ chức giáo dục khoa học và văn hoá liên hợp quốc",
    "UNICEF": "quỹ nhi đồng liên hợp quốc",
    "BTV": "biên tập viên",
    "MC": "người dẫn chương trình",
    "NSND": "nghệ sĩ nhân dân",
    "NSƯT": "nghệ sĩ ưu tú",
    "SV": "sinh viên",
    "HS": "học sinh",
    "NV": "nhân viên",
    "KCN": "khu công nghiệp",
    "KCX": "khu chế xuất",
    "BOT": "xây dựng kinh doanh chuyển giao",
    "BT": "xây dựng chuyển giao",
    "PPP": "hợp tác công tư",
    "ATM": "máy rút tiền tự động",
    "POS": "máy quẹt thẻ",
    "QR": "mã phản hồi nhanh",
    "STK": "số tài khoản",
    "VCB": "ngân hàng Ngoại thương",
    "BIDV": "ngân hàng Đầu tư và Phát triển",
    "TCB": "ngân hàng Kỹ thương",
    "MB": "ngân hàng Quân đội",
    "VPB": "ngân hàng Việt Nam Thịnh vượng",
    "ACB": "ngân hàng Á Châu",
    "SHB": "ngân hàng Sài Gòn Hà Nội",
    "HDB": "ngân hàng Phát triển Thành phố Hồ Chí Minh",
    "STB": "ngân hàng Sài Gòn Thương tín",
    "TPB": "ngân hàng Tiên Phong",
    "MSB": "ngân hàng Hàng hải",
    "OCB": "ngân hàng Phương Đông",
    "VIB": "ngân hàng Quốc tế",
    "SCB": "ngân hàng Sài Gòn",
    "TP.HCM": "Thành phố Hồ Chí Minh",
    "HN": "Hà Nội",
    "ĐN": "Đà Nẵng",
    "HP": "Hải Phòng",
    "CT": "Cần Thơ",
    "SG": "Sài Gòn",
    "QL": "quốc lộ",
    "TL": "tỉnh lộ",
    "BX": "bến xe",
    "BV": "bệnh viện",
    "PK": "phòng khám",
    "KTS": "kiến trúc sư",
    "KS": "kỹ sư",
    "CN": "công nhân",
    "ThS.BS": "thạc sĩ bác sĩ",
    "PGS.TS": "phó giáo sư tiến sĩ",
    "GS.TS": "giáo sư tiến sĩ",
    "GS": "giáo sư",
    "TT": "trung tâm",
    "TNHH": "trách nhiệm hữu hạn",
    "CP": "cổ phần",
    "Cty": "công ty",
    "Tp": "thành phố",
    "Tx": "thị xã",
    "Tt": "thị trấn",
    "HĐQT": "hội đồng quản trị",
    "TGĐ": "tổng giám đốc",
    "PTGĐ": "phó tổng giám đốc",
    "TPKD": "trưởng phòng kinh doanh",
    "KTT": "kế toán trưởng",
    "KCS": "kiểm tra chất lượng sản phẩm",
    "CSDL": "cơ sở dữ liệu",
    "CNTT": "công nghệ thông tin",
    "ATGT": "an toàn giao thông",
    "ATVSTP": "an toàn vệ sinh thực phẩm",
    "TN&MT": "Tài nguyên và Môi trường",
    "GD&ĐT": "Giáo dục và Đào tạo",
    "KH&CN": "Khoa học và Công nghệ",
    "VH-TT-DL": "Văn hoá Thể thao và Du lịch",
    "LĐ-TB-XH": "Lao động Thương binh và Xã hội",
    "TTg": "Thủ tướng",
    "TTr": "Thanh tra",
    "TAND": "Tòa án nhân dân",
    "VKSND": "Viện kiểm sát nhân dân",
    "TH&CL": "Thương hiệu và Công luận",
    "TTXVN": "Thông tấn xã Việt Nam",
    "VOV": "Đài Tiếng nói Việt Nam",
    "VTV": "Đài Truyền hình Việt Nam",
    "HTV": "Đài Truyền hình Thành phố Hồ Chí Minh",
    "CSKH": "chăm sóc khách hàng",
    "GCN": "giấy chứng nhận",
    "GPLX": "giấy phép lái xe",
    "ĐKXD": "đăng ký xây dựng",
    "QLTT": "quản lý thị trường",
    "QLDA": "quản lý dự án",
    "P.": "phường",
    "Q.": "quận",
    "H.": "huyện",
    "T.": "tỉnh",
    "X.": "xã",
    "đ/c": "đồng chí",
    "K/g": "kính gửi",
    "v/v": "về việc",
    "tr.": "trang",
    "Nxb": "Nhà xuất bản",
    "KĐT": "khu đô thị",
    "KDC": "khu dân cư",
    "SXKD": "sản xuất kinh doanh",
    "HĐLĐ": "hợp đồng lao động",
    "TCT": "tổng công ty",
    "TĐ": "tập đoàn",
    "NĐ": "nghị định",
    "QĐ": "quyết định",
    "NQ": "nghị quyết",
    "CT": "chỉ thị",
    "TTLT": "thông tư liên tịch",
    "BTC": "Ban tổ chức",
}

# Bảng từ viết tắt NHIỀU NGHĨA (để thu thập mo_ho cho LLM và mặc định đánh vần)
VIET_TAT_MO_HO: dict[str, list[str]] = {
    "BTC": ["ban tổ chức", "bít côi"],
    "UV": ["ủy viên", "tia cực tím"],
    "CP": ["Chính phủ", "cổ phần", "cổ phiếu"],
    "ĐT": ["điện thoại", "đội tuyển", "đầu tư", "đào tạo"],
    "DV": ["dịch vụ", "đoàn viên"],
    "VĐ": ["vấn đề", "vận động"],
    "NS": ["ngân sách", "nghệ sĩ", "năng suất"],
    "KH": ["kế hoạch", "khách hàng", "khoa học"],
    "PT": ["phát triển", "phương tiện"],
    "HD": ["hướng dẫn", "hợp đồng"],
    "TG": ["thời gian", "thế giới", "tổng giám đốc"],
    "CD": ["công dân", "chiến dịch", "đĩa CD"],
    "PC": ["phiếu chuyển", "máy tính cá nhân"],
    "TC": ["tài chính", "tiêu chuẩn", "tổ chức"],
}
