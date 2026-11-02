---
name: album-cover-color-wall
description: Build colour-filtered N×N album-cover walls from a local image library. Use when the user asks for an album cover collage, a same-colour wall, a black wall, or a colour-sorted music-library visual.
license: MIT
metadata:
  short-description: 按颜色生成专辑封面墙
  version: 1.3.0
  author: Hermes Agent
  hermes:
    tags: [music-library, color, collage, lab, layout, black]
    category: creative
---

# 专辑封面色墙

把本地专辑封面按色系拼成 N×N 的墙。选片决定用哪些图，排序决定怎么摆。

## 运行前必须说明（公开分发时尤其重要）

在真正扫描文件之前，先向用户说明并确认：

1. 需要用户自行提供**本地专辑封面目录**。
2. 技能包不携带图片、歌单、歌名、歌手名、排名、索引或历史实测数据。
3. 脚本只读取用户指定的封面目录，不上传图片；中间文件写到用户指定位置。
4. 不要扫描桌面、音乐库或主目录等无关位置。路径不明确时先问。
5. 如果用户只是要公开分享技能，不要主动索取真实个人数据。

## 第一件事：先问喜爱依据

**在排喜爱顺序之前，必须先问用户属于哪种情况：**

### A. 有歌单或歌手排名

问用户提供：

- 歌单文本：每行 `歌名 - 歌手`；合作曲用 `歌手 A / 歌手 B`
- 或已有的喜爱歌手排名 JSON
- 或直接给一串喜爱歌手，按用户给出的顺序排名

使用：

```bash
python scripts/build_favourites.py --playlist playlist.txt --out artist-ranks.json
```

或：

```bash
python scripts/build_favourites.py --artists "歌手 A,歌手 B,歌手 C" --out artist-ranks.json
```

计算口径：

- 先按“歌名 + 歌手集合”去重。
- 每位歌手按出现在多少首**唯一歌曲**中计数。
- 合作曲中的每位歌手各计 1 次。
- 歌曲数多的歌手排名靠前；同数按歌手名稳定排序。
- 这只是**歌手亲和度代理值**，不是对单张专辑的直接喜好。

### B. 没有歌单或排名

**不能拿文件名排序冒充喜爱排序。** 给用户两个出口：

1. 让用户手动说几位最喜欢的歌手，生成 `artist-ranks.json`。
2. 不做喜爱模式，改用 `similar` 或 `gradient`。

确认后再进入颜色、尺寸和模式选择。

## 第二件事：确认图库

| 情况 | 动作 |
|---|---|
| 有封面目录和索引 | 直接筛色 |
| 有图、没索引 | 运行 `build_index.py` |
| 没有封面目录 | 停下来问路径，不要猜 |

## 正式流程

必须询问以下三项，并等待用户选择：

1. **颜色**：红 / 黄 / 绿 / 蓝 / 紫 / 橙 / 青 / 粉（品红）/ 黑，或自定义色相带。
2. **尺寸**：默认 4×4。
3. **模式**：喜爱 / 纯色·相似色 / 纯色·带渐变。

顺序必须是：

```text
确认喜爱依据和封面目录
→ 可选：build_favourites.py
→ build_index.py
→ dedupe.py
→ 选定颜色和尺寸后计算严格核心池
→ 不足时按颜色自动分级放宽到建议余量
→ 4 级仍不足才先问用户
→ 问模式
→ make_wall.py
```

不要在建池前用“最爱的少数歌手”预过滤；正确做法是**先在颜色池内按歌手亲和度排序**。最爱的歌手在这个颜色里可能只有几张，直接预过滤会凑不满 N²。

## 颜色规则

严格核心色默认值：

| 色系 | 严格核心规则 | 每级放宽 |
|---|---|---|
| 红 | Lab h 15–45，C ≥ 30 | 色相 ±8°，C −4 |
| 黄 | Lab h 85–115，C ≥ 25 | 色相 ±7°，C −4 |
| 绿 | Lab h 115–165，C ≥ 21 | 色相 ±8°，C −4 |
| 蓝 | Lab h 255–300，C ≥ 27 | 色相 ±8°，C −4 |
| 紫 | Lab h 285–315，C ≥ 28 | 色相 ±5°，C −3 |
| 橙 | Lab h 52–72，C ≥ 35 | 色相 ±6°，C −4 |
| 青 | Lab h 188–212，C ≥ 27 | 色相 ±8°，C −4 |
| 粉 / 品红 | Lab h 335–5，C ≥ 25 | 紫侧每级 −6°；红侧每级 +2°，累计最多 +5°；C −3 |
| **黑** | **Lab L ≤ 28 且 C ≤ 40** | L 上限 +2、C 上限 +5 |

粉色放宽是非对称的：可以适度向紫侧扩张，但红侧最多到约 `h=10°`，避免重新混入红封面。`pink` 是 `magenta` 的别名。

### 自动放宽

默认先使用严格核心色。若池子小于建议目标：

```text
target = N² + max(8, ceil(N² / 2))
```

则按该颜色自己的步长逐级放宽，最多 4 级。4×4 的建议目标是 24 张，不是仅仅凑到 16 张，这样相似色、渐变和喜爱模式仍有选择余地。

成功报告会包含：

- `core_pool`：严格核心池大小
- `pool`：最终池大小
- `relax_level`：实际放宽级别
- `auto_relaxed`：是否自动放宽
- `auto_relax_target`：建议余量目标
- `effective_rule`：实际生效的色相/彩度/黑白阈值

`--relax N` 用于手动指定放宽级别，指定后不再自动放宽。`--no-auto-relax` 强制只用严格核心色。4 级后仍不足 `N²` 才返回退出码 3 询问用户。

`--min-clean` 只适用于有 `clean` 统计的预定义色相颜色；黑色请用 `--cmin` 增加彩度下限，自定义 `--hue` 也不能使用 `--min-clean`。

## 索引与文件名

默认解析：

```text
编号_歌手_专辑.ext
```

有歧义时使用：

```bash
python scripts/build_index.py --src <封面目录> --out index.json \
  --filename-regex "^(?P<artist>.*?)_(?P<album>.+)$"
```

正则必须包含命名组 `album`，`artist` 可选。

索引成功后输出单行 JSON，包含 `indexed`、`failed`、`metadata_matches` 等字段。坏图会跳过，不会让整批失败。

## 喜爱模式口径

运行时使用：

```bash
python scripts/make_wall.py ... \
  --artist-ranks artist-ranks.json
```

选片顺序：

1. 先建立颜色池。
2. 每张专辑取 `artist` 字段中匹配到的**排名最靠前的歌手**作为亲和度。
3. 同一主歌手只保留一张。
4. 按歌手亲和度升序，再按歌手歌曲数降序、专辑名排序。
5. 不足 N² 时从颜色池按相似色补位，补位项标 `*`。

兼容旧数据：`build_index.py --static album-rank.json` 仍支持逐专辑 `rank`，但新流程优先使用歌单推断出的歌手排名。没有有效歌手/专辑排名时，`fav` 会明确失败。

示例歌手排名文件见 `assets/artist-ranks-example.json`。

## 去重

判定顺序：

1. MD5 完全相同：直接判重。
2. pHash 汉明距离 ≤ 6。
3. Lab 平均色距离 ≤ 12。
4. 灰度结构相关系数 ≥ 0.92。
5. 灰度平均绝对误差 ≤ 0.08。

只有全部通过才按近似重复处理。去重必须在建池之前。`invalid` 条目在出墙时也会排除。

## 三种模式

| 模式 | 做法 |
|---|---|
| `fav` | 颜色池内按歌手亲和度排序、同主歌手去重、不足补位 |
| `similar` | 取离池中心 Lab 距离最近的 N² 张 |
| `gradient` | 为 N² 个明度锚点建立代价矩阵，用匈牙利算法全局最优分配 |

`gradient` 代价同时包含明度锚点、色相接近度和池中心距离，不是单纯一维排序。

## 内部排序

- 使用 2D 网格邻接，不按一维序号优化。
- 主轴为 `L + 0.25×C`。
- 行内左→右不增，列内上→下不增，即亮到暗。
- 只做不破坏单调约束的同列上下交换。
- 报告必须包含 `monotone: true`；不成立就停止出图。

## 出图

- 每格默认 400 px，输出 JPEG q93。
- 封面居中裁切成正方形，不拉伸。
- 底部只显示编号和专辑名，按实际像素宽度省略。
- 喜爱补位项末尾加 `*`。
- 输出目录不存在时自动创建，JSON 采用原子写入。

## 脚本

跨平台统一用 `python`；Linux 环境只有 `python3` 时替换为 `python3`。

```bash
# 1. 从歌单或手动歌手列表建立喜爱歌手排名
python scripts/build_favourites.py --playlist playlist.txt --out artist-ranks.json

# 2. 建索引（只读取 --src；可加 --recursive）
python scripts/build_index.py --src <封面目录> --out index.json \
  [--recursive] [--filename-regex "..."]

# 3. 去重
python scripts/dedupe.py --index index.json --out dedup.json

# 4. 出墙
python scripts/make_wall.py --index index.json --dedup dedup.json \
  --artist-ranks artist-ranks.json \
  --color black --mode fav --size 4 \
  --out black-wall.jpg --json black-wall.json

# 5. 无个人数据的合成自测
python scripts/selftest.py
```

### 参数速查

| 参数 | 作用 |
|---|---|
| `--artist-ranks` | 歌单推断或手动的喜爱歌手排名 |
| `--static` | 兼容旧版逐专辑排名；新流程不优先使用 |
| `--color` | 红/黄/绿/蓝/紫/橙/青/粉（品红）/黑 |
| `--hue 248-288` | 自定义 Lab 色相带，支持跨 0° |
| `--cmin 30` | 自定义彩度下限 |
| `--min-clean 0.25` | 预定义色相颜色的干净度硬筛 |
| `--relax 2` | 手动指定放宽到第 2 级，并关闭自动放宽 |
| `--no-auto-relax` | 只用严格核心色，不自动放宽 |
| `--cell 400` | 每格边长，范围 96–1600 |
| `--size 4` | N×N 的 N，范围 1–10 |

### 退出码

- `0`：成功。
- `2`：参数错误。
- `3`：色池不足，需要询问用户。
- `4`：文件、JSON、元数据或喜爱依据错误。

显式传入但不存在的 `--static`、`--artist-ranks`、`--dedup` 或 `--skip` 会直接报错，不会静默忽略。

## 已知局限

1. 歌手歌曲数只是喜爱代理值，不等于单张专辑质量或偏好。
2. 极端黑底白字封面会让平均 L 偏向中灰。
3. `disp` 对风景类封面不可靠，只作参考。
4. 喜爱模式会牺牲部分排序平滑度。
5. 单色系素材未必能形成连续渐变。
6. 黑色规则是 L/C 阈值，仍可能混入少量深色彩色封面。
7. 自动放宽会让同一颜色在不同图库、不同尺寸下使用不同有效阈值；报告中的 `effective_rule` 才是本次真实口径。
8. 文件名自动拆分有歧义时使用 `--filename-regex`。

## 依赖

- Python 3.9+
- Pillow >= 9.1
- numpy

不需要 scipy 或 sklearn。

## 参考

- `references/sop.md`：公开版执行 SOP
- `references/color-science.md`：Lab/HSV、干净度、2D 能量和分配代价说明
- `references/measured.md`：如何自行记录测量结果；不包含本机历史数据