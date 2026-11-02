# 专辑封面色墙 · 公开版 SOP

## 0. 开跑前

先说明并确认：

1. 用户提供本地封面目录。
2. 喜爱模式需要歌单、喜爱歌手排名或手动歌手名单。
3. 脚本只读指定目录，不上传图片。
4. 技能包不携带任何个人图片、歌单、排名或历史统计。

然后先问**喜爱依据**，不要先假装有一份排名。

| 用户情况 | 动作 |
|---|---|
| 有歌单文本 | `build_favourites.py --playlist` 统计唯一歌曲中的歌手出现次数 |
| 有喜爱歌手排名 JSON | 直接交给 `make_wall.py --artist-ranks` |
| 没有文件，但能口述歌手 | `build_favourites.py --artists "A,B,C"` |
| 没有喜爱依据 | 明确改用 `similar` 或 `gradient`，不做假喜爱排序 |

合作曲每位歌手各计 1 次。歌手歌曲数只是亲和度代理，不是专辑评分。

## 1. 问颜色

默认提供：红 / 黄 / 绿 / 蓝 / 紫 / 橙 / 青 / 粉（品红）/ 黑，并追问是否要其他颜色。

严格核心色：

| 色系 | 核心规则 |
|---|---|
| 红 | h 15–45，C ≥ 30 |
| 黄 | h 85–115，C ≥ 25 |
| 绿 | h 115–165，C ≥ 21 |
| 蓝 | h 255–300，C ≥ 27 |
| 紫 | h 285–315，C ≥ 28 |
| 橙 | h 52–72，C ≥ 35 |
| 青 | h 188–212，C ≥ 27 |
| 粉 / 品红 | h 335–5，C ≥ 25 |
| 黑 | L ≤ 28，C ≤ 40 |

先使用严格核心池。若小于 `N² + max(8, ceil(N²/2))`，按颜色自己的步长自动放宽，最多 4 级。粉色只向紫侧明显放宽，红侧最多加约 5°，避免吃进红色。

报告中的 `core_pool`、`pool`、`relax_level`、`auto_relaxed` 和 `effective_rule` 必须检查。

## 2. 问尺寸

默认 4×4，支持 1–10。**尺寸确定后再判断池子是否足够**，因为 N² 取决于尺寸。

## 3. 建索引与去重

```bash
python scripts/build_index.py --src <封面目录> --out index.json
python scripts/dedupe.py --index index.json --out dedup.json
```

坏图跳过；显式传入但不存在的文件直接报错。

去重条件：MD5 相同，或同时满足 pHash ≤ 6、Lab ≤ 12、灰度相关 ≥ 0.92、灰度 MAE ≤ 0.08。

## 4. 问模式

| 模式 | 行为 |
|---|---|
| 喜爱 | 颜色池内按歌手亲和度排序，同主歌手一张，不足补位 |
| 相似色 | 离池中心最近 |
| 带渐变 | 明度锚点全局最优分配 |

## 5. 出墙与检查

```bash
python scripts/make_wall.py --index index.json --dedup dedup.json \
  --artist-ranks artist-ranks.json \
  --color black --mode fav --size 4 \
  --out black-wall.jpg --json black-wall.json
```

检查：

- `picked == N²`
- `monotone == true`
- 标签只含编号和专辑名
- 补位项有 `*`
- 输出图无拉伸

## 6. 池子仍不足

自动放宽到第 4 级后仍不足 `N²`，才停下问用户：

1. 换颜色
2. 缩小尺寸
3. 接受更小的墙

自动放宽必须在报告中可见；不要偷偷改变阈值，也不要偷偷换模式。需要人工指定级别时用 `--relax N`，需要完全严格时用 `--no-auto-relax`。

## 7. 无个人数据的自测

```bash
python scripts/selftest.py
```

自测生成临时合成封面，覆盖红/绿/黑、严格核心与自动放宽、歌单推断、错误参数、坏图跳过和去重误判回归。