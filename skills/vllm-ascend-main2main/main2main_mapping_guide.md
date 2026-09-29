# vLLM Ascend Main2Main 映射指南

映射表用于缩小人工复核范围，不是自动修复清单。任何代码修改都必须先证明真实关系、真实契约变化、运行可达性和版本路径。

## 核心判定

候选只有同时满足以下条件，才允许标记为 `modify`：

```text
relationship_verified
AND contract_changed
AND runtime_reachable
AND version_lane_matches
```

含义如下：

- `relationship_verified`：当前代码证明存在继承/override、patch、真实callee、字段owner/access或明确复制协议。
- `contract_changed`：指定的上游old-to-new范围确实改变了该签名、字段schema、返回协议、生命周期或必须同步的语义。
- `runtime_reachable`：接口层分析中，源码已证明存在具体下游调用点或 patch/override 安装关系；本轮不要求进一步证明某个模型、设备或进程配置一定执行该路径。
- `version_lane_matches`：变更属于选定的main/release路径及其anchor。

只要有一项为false或unknown，就只能是 `review` 或 `dismiss`。

## Manifest 是唯一表清单

`main2main_mapping_manifest.json` 显式声明生产表、用途、默认阶段、是否启用和最低证据。分析器不得使用 `main2main_*_mapping.json` 通配符自动发现表，否则PR专用文件、历史fixture或优化暂存文件可能被误加载。

默认 profile 为 `exact-contracts`，只启用 method 表和动态精确检测，只输出：

- 当前源码可重新证明关系的函数/方法接口变化；
- 可执行的直接导入路径/符号删除或 Git 可证明的迁移；
- 同 owner 下唯一实现指纹可证明的函数改名；
- 下游精确调用点的目标、参数绑定和已证明的返回值消费变化；
- 已证明 patch/override 的安装输入签名和返回替代性变化。

字段、legacy 静态调用协议、注册、继承、broad、泛化 copied-file 和 changed-owner
线索默认不运行。只有用户明确要求下一阶段时才使用 `--profile expanded`。

生产表共六类：

| 表 | 主要问题 | 默认用途 |
|---|---|---|
| method | Ascend是否真正override或patch了变化的上游方法 | 默认启用 |
| field | Ascend是否依赖变化的上游字段契约 | expanded |
| dynamic exact call/return | 精确下游调用参数、返回消费及 patch/override 返回替代性 | 默认 exact-contracts |
| legacy call protocol | 静态表中的上游参数/调用协议线索 | expanded |
| registration | 被影响patch是否仍由正确生命周期加载 | expanded，review-only |
| inheritance | 父类/MRO变化是否可能影响Ascend子类 | expanded，P2 |
| broad | 文件/import/符号层面的弱关联 | 默认和 expanded 均不启用 |

PR专用表不能进入生产manifest，应放入测试fixture或归档目录。

## 1. Method mapping

Method表应区分以下关系：

| 关系 | 必须证明 | 最高优先级 |
|---|---|---:|
| direct override | 当前MRO中直接基类和下游同名定义 | P0/P1 |
| indirect override | 完整MRO和真正被覆盖的方法owner | P0/P1 |
| replacement patch | patch赋值完全替换目标，且安装链可达 | P0/P1 |
| wrapper patch | wrapper调用原函数，验证两侧签名/返回协议 | P0/P1 |
| alias rebind | import alias、赋值时机和使用点 | P1 |
| instance patch | 具体实例、`MethodType`和生命周期 | P1 |
| module attribute patch | 解析后的上游模块属性赋值、下游 callable、作用域和 guard | P0/P1 |
| patch call closure | 已验证 patch 调用链内唯一对应的下游 helper | P0/P1 |
| same-name candidate | 只有名称或平行目录相同 | P2 |

### `same_name_protocol` 的正确含义

历史表中的 `same_name_protocol` 不能视为已证明协议，应降级为 `same_name_candidate`。

例如，上下游可能同时存在：

```text
vllm/.../gumbel.py::gumbel_sample
vllm_ascend/.../gumbel.py::gumbel_sample
```

同名只能说明值得检查，不能证明：

- Ascend继承或patch了上游实现；
- 两者由同一个调用者按同一签名调用；
- CUDA/Triton内部语义变化需要NPU实现同步。

只有补充共同调用协议、注册替换、继承、patch或明确 `adapted from` 证据后，才可升级关系。

### Method触发规则

模块属性 patch 必须通过 AST 解析赋值两侧，支持 `vllm.x.y.fn = local_fn`
以及上游模块 alias。记录 `binding.kind/target/replacement/scope/guard`，多个赋值点
聚合到 `occurrences`。从 verified patch 延伸到本地 helper 时，必须证明调用可达、
下游模块对应关系和上游 callable 唯一性，使用 `patch_call_closure`，不得退化为裸同名。

`__init__` 只有在构造调用绑定已证明或显式调用 `super().__init__` 时才算有效 override；
单独存在 `**kwargs` 只证明调用语法可接受，不能证明函数体会消费或继续转发改名后的关键字。
只有构造函数把 `**kwargs` 原样转发给 `super()`，且上游仅新增可选参数时，才应判为已兼容。

动态发现 override 时，必须沿所有可由静态导入证明的上游基类解析完整
MRO，定位实际生效的方法 owner，并比较 old/new 的 owner-scoped AST 参数形状；
不得要求该方法必须定义在 Ascend 直接导入的基类上。

类方法映射必须发布 `owner_scoped_callable` trigger，记录上游 owner、callable
及完整限定名。预测阶段必须比较 old/new 中该 owner 作用域内的目标方法 AST；同一文件中
其他类出现同名方法，不构成当前映射的契约变化，也不得触发 registration closure。

必须定位到old/new中的真实符号变化，包括：

- required/optional参数变化；
- positional/keyword-only种类变化；
- rename、remove、move；
- 返回对象或异常协议变化；
- wrapper依赖的内部语义变化。

仅“文件变化”或“方法名出现在diff文本中”不满足 `contract_changed`。

函数接口必须使用 owner-scoped AST 参数形状比较；格式、换行和仅注解变化不触发。
函数改名只有在同一 owner 下存在唯一、实现 AST 完全一致的新 callable 时才可自动确认。
new 版本中新出现、同时被 Ascend override 的 callable 也要比较；Ascend 已接受新契约时降级。

预测器还应从当前 Ascend 继承关系中动态发现表外 direct override，并只保留本次
old/new 范围内该 owner 方法的签名、删除或迁移变化。若当前 Ascend override 的参数
契约已经与 new 版本完全一致，应标记为“已兼容”并降级，不进入核心修改点。

### 导入迁移与复制代码契约
- `import vllm` 后通过 `vllm.x.y.symbol` 使用的完整属性链属于精确导入引用。
  先在 old tree 中解析最长的真实模块前缀，再对该模块文件应用 Git rename 证据。
- 字符串模块路径以及 `vllm_version_is(...)` 分支内的属性赋值不进入默认导入结果；
  后者交给能解析赋值方向和条件的 patch-binding 检测。

- 当前 Ascend 直接导入的 vLLM 模块在 old 存在、new 不存在时，构成已验证的
  import-contract delta；必须输出具体模块、符号和消费文件。
- 新路径仅通过 Git rename 证据自动确认，并输出 `old_module -> new_module`；没有唯一
  rename 证据时只报告“旧路径删除、目标未知”。
- 直接导入 owner 的 AST 发生变化只进入复核清单，不能仅凭“owner 有变化”自动修改。
- 字符串模块路径必须继续验证真实 loader/注册链，单独出现不能满足强关系门槛。
- `Copied from` / `Adapted from` 文件注释本身不能证明某个自由函数必须同步。
  默认 profile 还要求存在 `module.function = function` 的同名绑定，并确认该上游函数
  参数形状变化或存在唯一精确改名；仅本地复用的 copied helper 不输出。

## 2. Field mapping

Field表只应描述vLLM拥有的字段契约，不应收录任意属性表达式。

每条语义边至少记录：

```text
canonical owner FQCN
field name
definition file
field type/default/optionality
Ascend access file and symbol
access kind: read|write|mutate|construct|forward
source SHAs and validation status
occurrences
```

### 必须验证owner

`vllm.config.VllmConfig` 可能只是re-export，真实owner在其他模块。判断字段变化时必须定位定义类，不能把re-export文件的任何diff都当成字段变化。

变量名推断也不能独立证明类型。例如 `config.foo` 不能仅因变量名含 `config` 就绑定到 `VllmConfig.foo`。

### 有效触发

- 字段新增、删除或改名；
- 类型、默认值或可空性变化；
- 可变对象变为不可变对象，或相反；
- 构造时required/optional变化；
- Ascend执行write/mutate，而上游对象结构发生变化。

### 应删除或隔离的噪声

- `tl.*`、`triton.*`、`logger.*`；
- `torch.*`、`F.*`、`math.*`、`np.*`等通用外部API属性；
- 只因字符串同名建立、但无法确定owner的字段；
- 同一语义边按行重复的记录。

同一关系只保留一条，所有使用位置聚合到 `occurrences`。

## 3. 调用与实现接口契约

### 3A. 默认动态精确契约

默认分析分为两个方向、四类核心契约：

| 方向 | 关系 | 契约 |
|---|---|---|
| 下游调用上游 | `direct_call` | 调用目标存在性与具体参数绑定；已证明的返回值消费 |
| 上游契约到下游实现 | `monkey_patch` / `override` | 安装后输入签名替代性；下游返回协议的保守结构协变 |

对 imported、annotated 或 constructed vLLM receiver，old/new 只沿唯一可证明的单继承链分别解析。下游 `self`/`super` 先由固定 vllm-ascend 基线的完整 MRO 证明一个有效上游 owner，再在 old/new 验证同一 owner；owner 移动时保持 unknown。调用点本身不能唯一解析时不生成 dependency；已证明 dependency 的端点、运行签名或受约束返回协议不可证明时输出 `analysis_unresolved`。未使用、直接转发或逃逸的返回值不生成返回消费 finding。

### 3B. expanded legacy call protocol mapping

Call表检查的是：本次上游范围改变了一个调用点的参数协议，而运行时实际绑定的Ascend实现是否能接受该协议。

正确关系是：

```text
changed callsite
-> resolved upstream callee
-> proven Ascend replacement/implementation
-> effective signature
```

### 必须验证callee

不能按函数名或类名搜索同名Ascend符号。以下做法会误报：

```text
上游 FusedMoE(...) 构造变化
-> 找到 AscendFusedMoE
-> 将构造参数与它的所有成员方法比较
```

构造调用必须解析：

- `__new__`；
- 当前MRO的有效 `__init__`；
- dataclass生成构造器；
- `*args`/`**kwargs`；
- CustomOp/plugin/patch之后真正实例化的类型。

普通函数调用同样必须解析import alias、属性owner和运行时替换关系。

### 有效触发

只比较指定old-to-new范围新增、删除或改变的参数：

- 新增必传keyword而Ascend有效签名不接收；
- 参数改名或从位置参数变为keyword-only；
- `**kwargs`被收紧；
- 调用者开始依赖新的返回协议。

历史表中保存的 `missing_keywords` 只能提示重新验证，不能作为当前失败证据。

## 4. Registration mapping

Registration表用于确认patch是否真正生效，不直接说明代码应该修改。

典型生命周期为：

```text
package/plugin entrypoint
-> vllm_ascend initializer
-> patch registry/import
-> installer
-> patch assignment/registration
-> target used in parent or engine-core process
```

还需要记录：

- version/device/feature条件；
- 父进程、worker或engine-core子进程范围；
- eager import还是延迟安装；
- replacement目标和卸载/移除边界。

### 使用规则

Registration只能作为 `review-only closure`：

1. method/field/call先产生已经验证的候选，或上游PR明确改变插件/平台生命周期；
2. 沿安装链检查patch仍会加载；
3. 只有安装链本身发生断裂时，相关入口才成为独立契约变化候选。

“patch文件被命中”不等于“必须修改 `__init__.py`”。

## 5. Inheritance mapping

Inheritance表主要识别类和MRO关系，不是专门识别继承字段。

它用于检查：

- 父类构造器变化；
- abstract method新增或签名变化；
- Ascend override落后于父类协议；
- 父类返回协议或生命周期变化。

纯继承关系本身是P2。若父类方法被Ascend override，应由method表提供强证据。若Ascend访问继承字段，需要同时满足：

```text
inheritance/MRO proof
+ canonical field owner proof
+ Ascend access proof
+ upstream field contract delta
```

## 6. Broad mapping

Broad表包含文件、import和符号级弱关联，召回率高但因果证据不足。

它只用于最终附录回答“是否还有模块未复核”，不得：

- 进入P0/P1；
- 自动生成修改任务；
- 因同文件或import命中而满足 `relationship_verified`。

## 分析流程

### A. 先确定准确差异范围

Main2main使用准确old/new SHA。上游PR分析必须比较：

```text
PR parent -> PR merge/head
```

不能用当前main与PR head的全量差异，否则会把历史变化归因给当前PR。

差异应覆盖Python代码之外的契约源，例如依赖、构建、注册和必要的C++/kernel接口；分析范围不能固定为 `-- vllm` 后就假设完整。

### B. Predict之前必须Validate

顺序固定为：

```text
manifest/provenance check
-> mapping validate on exact repositories
-> upstream contract delta extraction
-> candidate prediction
-> action-gate review
```

失效文件、失效符号、解除的继承、消失的patch赋值、错误owner和未解析callee必须在预测前隔离。

### C. 结果分层

- P0/P1 actionable：四项action gate全部通过。
- P2 review：关系可能存在，但契约变化、可达性或版本路径尚未证明。
- dismissed：已证明无依赖、已兼容、版本不匹配或与本次范围无关。
- appendix：broad和其他低信号候选。

文件分数不能覆盖因果证据。多条弱命中不能自动叠加为强关系，一条错误高分也不能把整个文件升级为actionable。

## 映射表优化和发布

优化过程分为“暂存输出”和“发布canonical表”两步。

### 1. 生成暂存输出

优化器从manifest的 `source_file` 读取不可变的原始候选表，并合并按source SHA验证的 `additional_source_files`；生产分析只读取 `file` 指向的canonical表。优化器执行：

- 按语义边去重，并把源位置聚合到 `occurrences`；
- 删除明确的外部API/日志噪声；
- 将same-name和未解析关系降级为P2/review；
- 校验目标文件、符号及可静态证明的关系；
- 记录输入、输出、删除、去重和降级统计；
- 写入source SHAs、生成器、生成时间和validation status。

暂存输出路径由manifest指定，生成时不得覆盖canonical表。

### 2. 检查和人工复核

```powershell
python .\scripts\optimize_mappings.py `
  --mapping-dir . `
  --ascend-root <vllm_ascend_repo> `
  --vllm-root <vllm_repo>

python -m pytest -q .\tests\test_optimize_mappings.py

python .\scripts\optimize_mappings.py `
  --mapping-dir . `
  --ascend-root <vllm_ascend_repo> `
  --vllm-root <vllm_repo> `
  --check
```

重点复核删除、降级和仍未解析的记录。优化器不能凭启发式把P2提升为强关系。

### 3. 发布

人工确认后，将已验证暂存内容发布到manifest记录的canonical文件名，更新provenance和完整性信息，然后再次执行：

```text
validate -> predict
```

如果当前表与记录的source SHA不匹配，应fail closed，不得继续输出actionable候选。

## 评价方法

`predicted files ∩ actual changed files` 只能衡量搜索范围，不能衡量根因准确性。同文件但错误符号、错误关系或错误上游PR不是真命中。

推荐评价单位：

```text
upstream PR/range
+ changed symbol/contract
+ verified Ascend relation
+ actual fix cause
```

未行动候选应分类为：

- already compatible；
- historical debt；
- forward backport；
- unrelated cleanup；
- test-only/defensive；
- intentionally unsupported；
- infrastructure/flaky；
- unknown。

建议持续观察：

- causal root-cause recall；
- P0/P1 precision和recall；
- precision@10；
- 找到最后一个真阳性前需要复核的候选数；
- 误报人工复核时间；
- CI失败根因召回率。

## 标准报告字段

每条候选至少输出：

| 字段 | 内容 |
|---|---|
| upstream | PR/range、文件、符号、hunk和契约变化 |
| Ascend | 文件、符号、关系及证明 |
| mapping | 表、记录ID、validation status |
| reachability | feature/device/process路径 |
| version lane | main/release及anchor |
| gate | 四项布尔值 |
| action | `modify|review|dismiss` |
| evidence | CI job/test/log或静态证据 |
| reason | 修改或dismiss原因 |

## 总结

需要保留的是六类检测能力，而不是无条件信任现有静态记录：

- method保留真实override/patch；same-name降级为P2；
- field保留canonical owner字段契约，删除通用属性噪声；
- call重新绑定真实callee，只比较本次参数delta；
- registration保留为review-only生命周期闭包；
- inheritance保留为P2和method/field的关系证明；
- broad仅作为附录查漏。

最终目标不是输出更多文件，而是以较小的可审计候选集覆盖真实上游契约变化，并确保只有通过四项action gate的候选进入main2main修改范围。
