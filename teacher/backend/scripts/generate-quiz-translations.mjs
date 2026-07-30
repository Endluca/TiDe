import fs from "node:fs/promises";

const inputPath = new URL("../reference/task-quiz-banks.json", import.meta.url);
const separator = "__QZSEP_71936__";
const maxBatchCharacters = 3600;

const cleanSourceText = (value) => String(value)
  .replace(/Page\s+\d+\s+of\s+\d+/gi, "")
  .replaceAll("otherdisciplines", "other disciplines")
  .replaceAll("anobstacle", "an obstacle")
  .replaceAll("int he classroom", "in the classroom")
  .replaceAll("inthe classroom", "in the classroom")
  .replaceAll("Teaching Readiness Deamo", "Teaching Readiness Demo")
  .replaceAll("What is can discourage", "What can discourage")
  .replace(/\s+([.,!?])/g, "$1")
  .replace(/\s{2,}/g, " ")
  .trim();

const polishChinese = (value) => String(value)
  .replaceAll("免费试听课程", "体验课")
  .replaceAll("免费试听课", "体验课")
  .replaceAll("免费试用教师", "体验课教师")
  .replaceAll("免费试用老师", "体验课教师")
  .replaceAll("免费试用级别评估", "体验课级别评估")
  .replaceAll("免费试用", "体验课")
  .replaceAll("51talk", "51Talk")
  .replaceAll("切换材质", "切换教材")
  .replaceAll("预热页", "热身页")
  .replaceAll("扉页", "标题页")
  .replaceAll("演示页面", "呈现页")
  .replaceAll("制作页面", "产出页")
  .replaceAll("免费谈话", "自由交谈")
  .replaceAll("防风草", "PARSNIP")
  .replaceAll("手指钻", "手指操练")
  .replaceAll("替换钻", "替换操练")
  .replaceAll("表情符号练习", "表情操练")
  .replaceAll("课程费用便宜", "课程价格低")
  .replaceAll("一个令人困惑的教训", "一堂令人困惑的课")
  .replaceAll("级别评估页面", "级别评估表")
  .replaceAll("等级评估", "级别评估")
  .replaceAll("学生的水平取决于所学习材料的水平", "学生级别根据本次使用教材的难度判断")
  .trim();

const manualOverrides = {
  "course-400-q1": {
    questionZh: "Teaching Readiness Demo 的另一个名称是什么？",
    optionsZh: ["Mouth Demo（口型演示）", "Mark Out Demo（标记演示）", "Monke Demo", "Mock Demo（模拟演示）"],
  },
  "course-400-q2": {
    questionZh: "谁会参加你的 Teaching Readiness Demo？",
    optionsZh: ["我的招聘专员", "我的中文评估员", "我的培训师", "公司总裁"],
  },
  "course-400-q3": {
    questionZh: "你什么时候会收到 Teaching Readiness Demo 的用户名和密码？",
  },
  "course-400-q5": {
    questionZh: "以下哪一项对完成 Teaching Readiness Demo 没有帮助？",
    optionsZh: ["练习 TPR", "准备教具", "提出概念检查问题（CCQ）", "把所有内容直接读给学生"],
  },
  "course-500-q4": {
    questionZh: "在课堂指令中，KISS 原则代表什么？",
    optionsZh: ["保持简短清晰", "让指令精彩而迅速", "保持直接、甜蜜和简单", "保持安全、微笑并成功"],
  },
  "course-398-q18": {
    questionZh: "判断题：教师不应与低龄学员讨论 PARSNIP 话题；年长学员接受度更高，因此可以与他们讨论。",
  },
  "course-398-q20": {
    questionZh: "选择合适的回应。学生：“老师，你知道我们国家有国王吗？”老师：“……”",
    optionsZh: ["“真的吗？我以前不知道。请再多告诉我一些。”", "“哇，很有意思。好的，我们继续看这一页……”"],
  },
  "course-324-q4": {
    questionZh: "KISS 原则是什么意思？",
    optionsZh: ["保持简短清晰", "保持互动相似且合适", "保持令人满意且理智", "情侣之间的浪漫动作"],
  },
  "course-510-q3": {
    questionZh: "SANDWICH 反馈法包含哪三个部分？",
    optionsZh: ["面包、肉饼和蔬菜", "整体优势、整体待改进点和建议", "学生的优势、待改进点和建议", "反馈和告别歌曲"],
  },
  "course-510-q11": {
    questionZh: "如果学生说完整句子时遇到困难，老师可以怎么做？",
    optionsZh: ["用口型提示学生", "给予奖励", "立即说出正确答案", "只微笑不提示"],
  },
  "course-510-q17": {
    questionZh: "在 Look and Say 活动中，除了让学生重复短语，还可以使用哪种操练方式？",
    optionsZh: ["拍手操练", "表情操练", "不进行任何操练", "重复操练"],
  },
  "course-510-q19": {
    questionZh: "哪种操练最适合帮助学生记住短语“play the guitar”？",
    optionsZh: ["手指操练", "老虎机操练", "替换操练", "表情操练"],
  },
  "course-630-q3": {
    questionZh: "一名 Level 2 学员点击了正确答案但没有开口。老师应该怎么做？",
    optionsZh: ["表扬点击并直接继续", "完全停止活动", "提示学生先说出目标单词或句子，再让其点击", "下次替学生点击"],
  },
  "course-630-q4": {
    questionZh: "一名 Level 1 学员只说了“monkey”，而目标句是“I see a monkey.”，老师应该如何回应？",
    optionsZh: ["不加练习，直接接受并继续", "说：“很好。现在我们来说完整句子：I see a monkey.”", "告诉学生答案错误", "让学生拼写 monkey"],
  },
  "course-630-q5": {
    questionZh: "老师问“What is it?”后，一名 Level 0 学员没有回应。老师首先应该怎么做？",
    optionsZh: ["立即翻到下一页", "给出很长的解释", "结合图片示范答案，再请学生跟读", "告诉学生这节课太难"],
  },
};

const translateBatch = async (items) => {
  const source = items.join(` ${separator} `);
  const query = new URLSearchParams({
    client: "gtx",
    sl: "en",
    tl: "zh-CN",
    dt: "t",
    q: source,
  });
  const response = await fetch(`https://translate.googleapis.com/translate_a/single?${query}`);
  if (!response.ok) throw new Error(`Translation request failed: ${response.status}`);
  const payload = await response.json();
  const translated = payload[0].map((segment) => segment[0]).join("");
  const results = translated.split(separator).map((item) => item.trim());
  if (results.length !== items.length) {
    throw new Error(`Translation batch mismatch: expected ${items.length}, received ${results.length}`);
  }
  return results;
};

const data = JSON.parse(await fs.readFile(inputPath, "utf8"));
const sourceStrings = [];
for (const questions of Object.values(data.taskQuizBanks)) {
  for (const question of questions) {
    question.question = cleanSourceText(question.question);
    question.options = question.options.map(cleanSourceText);
    sourceStrings.push(question.question, ...question.options);
  }
}

const uniqueStrings = [...new Set(sourceStrings)];
const batches = [];
let currentBatch = [];
let currentLength = 0;
for (const value of uniqueStrings) {
  const nextLength = currentLength + value.length + separator.length + 2;
  if (currentBatch.length > 0 && nextLength > maxBatchCharacters) {
    batches.push(currentBatch);
    currentBatch = [];
    currentLength = 0;
  }
  currentBatch.push(value);
  currentLength += value.length + separator.length + 2;
}
if (currentBatch.length > 0) batches.push(currentBatch);

const translations = new Map();
for (let index = 0; index < batches.length; index += 1) {
  const batch = batches[index];
  const results = await translateBatch(batch);
  batch.forEach((value, itemIndex) => translations.set(value, results[itemIndex]));
  process.stdout.write(`Translated batch ${index + 1}/${batches.length}\n`);
}

for (const questions of Object.values(data.taskQuizBanks)) {
  for (const question of questions) {
    question.questionZh = polishChinese(translations.get(question.question));
    question.optionsZh = question.options.map((option) => polishChinese(translations.get(option)));
    Object.assign(question, manualOverrides[question.id] || {});
  }
}

await fs.writeFile(inputPath, `${JSON.stringify(data, null, 2)}\n`, "utf8");
process.stdout.write(`Added Chinese copy for ${sourceStrings.length} question and option strings.\n`);
