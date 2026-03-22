我需要根据gemini-cli的源码，自制一个MT-3000平台的Agent for Coding
首先，MT-3000是超算迈创平台，异构架构，目前实现的功能是调用LLM的API进行简单的prompt拼接和问答，不能称之为Agent，没有ReAct循环。

总体来说，gemini-cli的ReAct循环核心可以分为如下层级:

A. 用户层
B. packages/cli 交互层
C. packages/core 内核层
D. LLM
E. 本地/外部工具

ReAct循环的主要步骤如下:

1. 用户输入prompt指令（MT-3000上对于核函数进行优化）
2. packages/cli接收到用户prompt，通过入口`useGeminiStream.submitQuery()`准备并发送查询
3. packages/core发起流式推理请求(sendMessageStream)给LLM处理

Phase1: 推理
4. LLM返回行动计划ToolCallRequest给packages/core内核层
5. packages/core内核层通过事件流传递计划给packages/cli层(调度: useReactToolScheduler)
6. packages/cli交互层向packages/core内核层请求执行工具(CoreToolScheduler.schedule)
6a. [optional] packages/core内核层接收到后，如果是高风险操作（比如shell）需要向用户请求确认（返回给packages/cli交互层）
6b. [optional] packages/cli交互层向用户层渲染对话框
6c. [optional] 用户层授权，结果返回给packages/cli交互层
6d. [optional] packages/cli交互层返回确认结果给packages/core内核层
7. packages/core内核层 执行 `本地/外部工具` 比如(WriteFile, RunShell)

Phase2: 行动
8. `本地/外部工具`返回执行结果给packages/core内核层（执行完成，触发回调）
9. packages/core内核层回调`onAllToolCallsComplete(results)`交给packages/cli交互层（闭环点:handleCompletedTools）

Phase3: 观察[ReAct循环]
10. packages/cli将工具结果作为新上下文，再次调用`submitQuery()`给packages/core内核层 {和第2步一样}
11. packages/core内核层发起新一轮推理给LLM {和第3步一样}


12. LLM返回最终答案（无工具调用）给packages/core内核层
13. packages/core内核层将最终答案传递给packages/cli交互层
14. packages/cli交互层渲染最终结果给用户层

现在，此项目的根目录下，有一个大致的开发计划`./PLAN.md`(不一定完全准确)
我们的技术栈主要用LangChain/LangGraph
gemini-cli的源码主要参考`~/Documents/code/projects/gemini-cli`
现在请你生成详细的开发计划，将目前的项目做成类似gemini-cli或者claude code的coding agent
生成的开发计划放置在项目根目录，命名`dev-plan.md`
请ultrathink: 