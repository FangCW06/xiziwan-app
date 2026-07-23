# F-A0021-001 潮汐預報 — 高雄市鼓山區 實測範例

## 查詢方式確認
- 正確 LocationName 寫法:**高雄市鼓山區**(縣市+鄉鎮區需完整合併,不能只寫「鼓山區」)
- LocationId: `64000020`
- 座標:Latitude 22.6144, Longitude 120.2883
- 查詢網址格式:
  ```
  https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/F-A0021-001?Authorization={你的授權碼}&format=JSON&LocationName=高雄市鼓山區
  ```
- ⚠️ 若不帶 LocationName 篩選,會回傳全臺灣資料,檔案巨大、查詢極慢,務必帶篩選條件

## 資料結構重點欄位

```
Location
├── LocationId, LocationName, Latitude, Longitude
└── TimePeriods.Daily[]  ← 每天一筆
    ├── Date            日期
    ├── LunarDate        農曆日期
    ├── TideRange        大潮 / 中潮 / 小潮(月週期,非每天固定)
    └── Time[]           當天的滿潮/乾潮時刻,通常 2-4 筆
        ├── DateTime     確切時間(含時區 +08:00)
        ├── Tide         "滿潮" 或 "乾潮"
        └── TideHeights
            ├── AboveTWVD        相對臺灣高程系統
            ├── AboveLocalMSL    相對當地平均海平面
            └── AboveChartDatum  相對海圖(建議採用此值,最低低潮位為0,數字越大水位越高)
```

## 實測範例(2026-07-15,大潮)

| 時刻 | 類型 | AboveChartDatum(cm) |
|------|------|---------------------|
| 07:43 | 滿潮 | 159 |
| 15:37 | 乾潮 | 32 |
| 21:35 | 滿潮 | 75 |

當天潮差高達 127cm(大潮特徵)。

## 對分級邏輯的意義

- `TideRange = "大"` 的日子,潮差大,退潮時水域範圍變化劇烈,退潮/乾潮前後時段適合標記為「需注意」
- 資料涵蓋未來約一個月,可以預先知道哪幾天是大潮,提前在 App 裡標示
- 資料本身不含「危險」判斷,純粹是潮位數字,危險與否的閾值仍需要自己訂(見 color-logic.md)

## 待辦
- [x] 確認潮汐資料格式與查詢方式
- [ ] 查詢 F-A0012(海面天氣預報,含風速)實際回傳格式
- [ ] 查詢育樂天氣預報系列中,西子灣/海水浴場對應的確切代碼
