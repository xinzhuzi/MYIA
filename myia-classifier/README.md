# myia-classifier

MYIA 七大类关键词分类引擎,独立 pip 包:零运行时依赖、免费/付费双信号裁决、数据与代码分离、命中可追溯。

从 MYIA 核心的分类层(`myia.classify`)拆出,忠实移植生产爬虫关键词分类器(2026-09-28 纠错版,金测集钉住语义)。可脱离 MYIA 单独引用,MYIA 核心也以固定版本范围依赖本包。

## 特性

- **零依赖**:纯 Python 标准库(3.11+),`pip install` 即跑,无任何传递依赖
- **双信号裁决**:免费信号绝对优先于付费信号;注册引流过滤;死词/噪声词过滤
- **数据与代码分离**:词表是包内 `data/keywords.json`,调关键词/正则不用改代码
- **命中追溯**:每条结果都带命中明细(哪张表、哪个关键词/正则、命中区间)
- **自定义规则 API**:`when` 表达式走白名单 AST,永不 `eval`,未知构造加载期即拒
- **类型完备**:全量类型标注(`py.typed`),mypy strict 友好

## 安装

尚未上架 PyPI;在那之前从 MYIA 仓库以 uv workspace 安装(`git clone https://github.com/xinzhuzi/MYIA && cd MYIA && uv sync`),PyPI 上架后即可 `pip install myia-classifier`。

## 最小示例

```python
from myia_classifier import classify_item

result = classify_item({"title": "Claude 免费领100$"})
print(result.category)  # "token"
print(result.tags)      # 自定义规则命中的 tag(无规则时为 [])
for evidence in result.matched:
    print(evidence.describe())  # 命中追溯: 表:类型=值
```

七大类:`credit-card` / `proxy-node` / `buying-agent` / `server` / `token` / `ai-news` / `freebie`;另有第八个生产标签 `channel`(付费信号且无免费信号的导购/渠道帖)。被过滤标题的 `category` 为 `None`。

## 自定义规则

规则读取条目字段(字典键或对象属性),在粗分类之上打 tag:

```python
from myia_classifier import Rule, classify_item

rule = Rule(name="big-move", when="abs(change_pct) >= 3", tag="大波动")
result = classify_item({"title": "NVDA 盘前大跌", "change_pct": -4.2}, rules=[rule])
print(result.tags)  # ["大波动"]
```

`when` 表达式支持的语法(白名单 AST):字面量、字段名、list/tuple、四则与比较运算、`and`/`or`/`not`、白名单函数(`abs/min/max/round/len/int/float/str`)。属性访问、下标、lambda、f-string、推导式、任意调用等一律在构造期抛 `RuleSyntaxError`。运行期单条规则失败只按"未命中"处理并记 WARNING,绝不打断整批评测。

## 自定义词表

默认词表随包分发(`myia_classifier/data/keywords.json`);也可以加载自己的 JSON:

```python
from myia_classifier import load_table, classify_title

table = load_table()  # 缺省即打包词表;自定义时传自己的 JSON 路径(结构非法时结构化报错)
print(classify_title("便宜出极速服务器三台", table).category)  # "server"(打包词表实测命中)
```

词表 JSON 支持在 `keywords`(普通关键词)与 `patterns`(正则)两列增改,`case_insensitive` 按节可调;顶层未知字段与断裂引用(如 `fallback_target` 指向不存在的类目)加载即报错,不做静默忽略。

## 从 YAML 加载规则

`load_rules` 可直接读取 MYIA 插件 YAML 的 `classify.rules` 节(需要环境里装有 PyYAML;本包不把它列为依赖):

```python
from myia_classifier import load_rules

rules = load_rules("plugin.yaml")
```

## 许可

MIT
