---
name: poster-dl
description: 按片名把电影、电视剧、动画、纪录片的海报下载到本地,画质可选(low / medium / high / original 原图),支持按年份和类型区分同名作品;配置 TMDB key 后还能下载分季海报、指定语言版本(中文/英文/无字版)、从多张候选里挑选。当用户说"下载某某的海报"、"帮我找某部电影/电视剧/番剧的海报"、"要高清海报"、"海报原图"、"第二季的海报"、"无字海报",或提到 movie poster、TV poster、download poster、cover art 时使用。
---

# 影视海报下载

脚本是本文件同目录下的 `scripts/poster_dl.py`,只依赖 Python 3 标准库,不交互,结果以 JSON 输出到 stdout。下文用 `$S` 代表它的绝对路径。

默认不需要任何 key(走 IMDb 搜索接口,每部作品一张主海报)。只有用户要分季海报、指定语言版本或想看多张候选时才需要 TMDB key,到那时再引导用户配置,平时不要主动提。

## 基本流程

1. 从用户的话里取出:片名、年份(可无)、电影还是剧集(可无)、画质(可无)。
2. 直接下载:

   ```bash
   python3 "$S" download "片名" [--year 2019] [--type movie|tv] [-q original] [--name "保存用的片名"]
   ```

3. 按返回的 `status` 处理(见下表),最后告诉用户文件路径和实际分辨率(`width`×`height`)。

片名用用户原话即可,中文、英文、原名都能搜。免 key 时数据源返回的是英文片名,所以用户说的是中文片名时加 `--name "用户说的片名"`,文件名才是中文。

## 画质

| 用户的说法 | 参数 | 宽度 |
|---|---|---|
| 缩略图、小图 | `-q low` | 342px |
| 普通、中等、发聊天用 | `-q medium` | 500px |
| 高清 | `-q high` | 780px |
| 原图、最高清、壁纸、打印,或没有说 | `-q original` | 源站原图,常见 2000×3000 |

用户没说画质时用 `original`,不必专门询问,回复里顺带说明用的是哪一档、还可以换哪几档。原图比所选档位小时脚本不会放大,会在 `warnings` 里说明,照实转告。

## 按 status 处理

| status | 含义 | 怎么做 |
|---|---|---|
| `ok` | 已保存 | 报告 `file` 和分辨率;有 `warnings` 或 `hint` 就转告 |
| `ambiguous` | 有多个候选 | 看 `candidates`:只有一个明显符合用户说法就直接用它;否则把候选的片名、年份、类型、`detail` 列给用户选。选定后 `download --id <候选的 id> --name "片名"` |
| `not_found` | 没搜到 | 换成原名或英文名再试一次,仍然没有再告诉用户 |
| `need_key` | 这个功能要 TMDB key | 说明是哪个功能需要,把 `guide.steps` 原样给用户;有 `without_key` 就同时给出不配 key 的替代做法 |
| `bad_key` | key 无效 | 请用户核对后重新 `config set-key` |
| `error` | 其他错误 | 转告 `message` |

搜索是模糊匹配,片名很冷门时返回的候选可能全都不相关——这种情况当作没找到处理,不要硬选一个。

## 需要 TMDB key 的功能

```bash
python3 "$S" download "片名" --season 2            # 第 2 季的海报(0 为特别篇)
python3 "$S" download "片名" --lang zh             # 语言版本: zh / en / ja / ko …,none 为无字版
python3 "$S" list "片名" [--lang zh] [--season 2]  # 列出全部版本,按评分排序
python3 "$S" download --id <id> --pick 3           # 下载 list 里的第 3 张(--lang/--season 要与 list 时一致)
```

配了 key 之后默认下载的是作品原语言里评分最高的一张,搜索中文片名时返回的也是中文片名。

用户把 key 发来后运行 `python3 "$S" config set-key <key>`,脚本会联网验证,通过才保存。key 存在本机 `~/.config/poster-dl/config.json`,所有 agent 共用;不要在回复里复述完整的 key。`python3 "$S" config` 可查看当前是否已配置。

## 其他选项

- `--out <目录>`:保存位置,默认 `~/Downloads/posters`。想长期换目录用 `config set-dir <目录>`。
- 同名文件不会被覆盖,会自动加 ` (2)` 这样的序号;确实要覆盖时加 `--force`。
- `--first`:有多个候选时不询问,直接取排在最前的一个。只在用户明确说"随便哪个都行"时用。
- `search "片名"`:只搜索不下载,用来先看候选。
- 一次要下载多部时逐部调用,每部各自处理候选。

## 写进 Notion(可选)

用户要求把海报放进 Notion 的观影清单之类的数据库,并且当前环境有 Notion 工具时,读 `references/notion.md`。
