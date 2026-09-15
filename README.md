# Herman Multi Strategy Executor

私有多策略自动交易执行器。当前把两套 HermanTrading 公开策略统一接入同一个 Python / Hyperliquid / OKX 执行框架，并通过本地交互终端选择要运行的策略。

> 当前版本标签是本项目自己的产品编号：**1.0 = Trend Rebalance Map**，**1.1 = Streak Failure Reversal**。它们不是 HermanTrading 原仓库的版本号。

## 策略来源

### 1.0 · Trend Rebalance Map

- 原作者：HermanTrading / @RHerman
- 上游策略：<https://github.com/HermanTrading/Trend-Rebalance-Map-Herman->
- 原始思路：均线拉伸后的回归交易

核心逻辑：

- SMA50 / SMA200
- LONG：`SMA50 < SMA200` 时，价格收盘重新上穿 SMA50
- SHORT：`SMA50 > SMA200` 时，价格收盘重新跌破 SMA50
- 两均线距离必须超过默认 30 points
- 默认 TP：SMA200 Dynamic
- 默认 SL：125 points
- 同时只管理一个仓位

### 1.1 · Streak Failure Reversal

- 原作者：HermanTrading / @RHerman
- 上游 Pine：<https://github.com/HermanTrading/Streak-Failure-Reversal-Herman-/blob/main/Streak%20Reversal%20Strategy%20%5BHerman%5D.txt>
- 原始思路：连续单边 K 线之后，等待结构失败确认再反向入场

默认逻辑：

- 1m 图表；Signal timeframe 可选 1m / 5m
- 默认连续 5 根 bullish / bearish candle bodies 构成 streak
- bullish streak 只会 **ARM SHORT**，不会立即做空
- 后续最多等待 15 根 signal candle
- SHORT 必须等后续确认 K **收盘跌破 terminal streak candle LOW**
- LONG 为完全镜像
- 影线单独突破不触发
- 默认 New York Session：09:45–12:00
- 默认 16:00 ET hard flat
- 默认 SL：Terminal streak candle extreme
- 默认 TP：1R
- SL / TP 在实际成交后冻结，不做移动止盈

## 两个策略底层区别

| 项目 | 1.0 Trend Rebalance | 1.1 Streak Failure Reversal |
|---|---|---|
| 核心范式 | 均值回归 | 动能衰竭 / Failure Reversal |
| Setup | SMA50 / SMA200 拉伸 | 连续同方向 K 线 |
| Trigger | 价格重新穿回 SMA50 | 后续收盘破坏 terminal streak 结构 |
| 入场确认 | 单阶段 crossover | 两阶段：ARM → confirmation |
| 有效窗口 | 无 setup expiry | 默认 15 根 signal bar |
| 时间过滤 | 无固定 Session | 默认 09:45–12:00 ET |
| 默认止盈 | Dynamic SMA200 | Frozen 1R |
| 默认止损 | Fixed 125 points | Terminal streak extreme |
| 更适合 | 拉伸后回归、震荡/回撤 | 连续冲刺后失败反转、日内 exhaustion |

## 交易所

- Hyperliquid HIP-3，默认：`xyz:XYZ100`
- OKX 线性 USDT / USDC perpetual swap
- DRY RUN / OKX DEMO / LIVE

## 本地运行

```bash
gh repo clone jonyjanytb-dev/herman-multi-strategy-executor
cd herman-multi-strategy-executor
bash run_local.sh
```

首次运行会从 `.env.example` 创建本地 `.env`。`.env` 已被 `.gitignore` 排除。

## 交互式终端

```text
Herman Multi Strategy Executor · Hyperliquid / OKX

1) 启动机器人
2) 选择策略
3) 设置每笔仓位 / 杠杆
4) 设置交易所 / API 凭证
5) 切换 DRY RUN / DEMO / LIVE
6) 设置做多 / 做空方向
7) 设置策略参数
8) 刷新状态
9) 查询资金 / 当前持仓
0) 退出
```

切换策略时：

1. 如果能检测到真实持仓，终端拒绝切换。
2. 自动切换到策略独立状态文件。
3. 自动恢复为 `DRY_RUN=true`，避免刚切策略就直接实盘。

状态文件默认隔离：

```text
runtime/state-hyperliquid-trend_rebalance.json
runtime/state-hyperliquid-streak_failure.json
runtime/state-okx-trend_rebalance.json
runtime/state-okx-streak_failure.json
```

## 1.1 对 Pine 的执行适配说明

信号、Setup、Confirmation、Session、Hard Flat、结构 SL、R target 等按上游 Pine 语义实现。

有两处是为了把 MNQ/Pine 执行模型迁移到 Hyperliquid / OKX 线性永续而做的工程适配：

- Pine 的 `Fixed contracts` 在不同交易所没有统一含义，所以默认继续使用项目原本的 **固定名义仓位** `ORDER_NOTIONAL_USDC`。
- 可选 `Stop-risk budget` 会按 `风险美元 ÷ 止损价格距离` 换算为线性合约名义仓位，并受 `STREAK_MAX_NOTIONAL_USD` 上限约束。

1.1 的 Pine 使用 `process_orders_on_close=false`：确认 K 收盘后，订单在下一可用 chart bar 开盘成交。实盘程序是在确认 K 已关闭后立刻发送订单，因此实际成交发生在下一根 1m bar 开始附近，但仍可能存在网络延迟和滑点。

## 安全设计

- 策略切换强制回到 DRY RUN
- 每个策略独立 runtime state
- Runtime state 损坏时拒绝静默重置
- 同一根已确认信号只尝试一次，不因 API 报错无限重复下单
- 开仓后等待真实持仓传播，再使用实际 entry price 生成 1.1 的最终 1R TP
- Hyperliquid / OKX 保护单仍优先依赖交易所原生 TP / SL
- Streak 策略实际 fill 若让原结构 SL/TP 失效，会立即请求平仓而不是制造虚假的一跳止损

## 测试

```bash
python -m pytest -q
```

当前测试覆盖：

- Trend Rebalance crossover / Dynamic SMA200 TP
- Streak ARM → confirmation
- “影线突破不算，必须 close break”
- Streak fill 后 1R 目标
- Gap 导致结构止损失效
- Stop-risk budget notional cap
- 5m signal candle 完整聚合
- Runtime state 原子保存与损坏 fail-closed

## 重要说明

这不是投资建议，也不是收益承诺。LIVE 模式会发送真实订单。Hyperliquid / OKX 的网络、交易所状态、滑点、精度、保证金与 API 行为都可能导致实盘和 TradingView 回测不同。

本仓库当前为 **Private Development**。上游策略思想归 HermanTrading / @RHerman；本项目工作重点是多策略状态机、交易所执行、保护单、终端交互和运行安全。
