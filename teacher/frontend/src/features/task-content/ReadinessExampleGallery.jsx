import { useEffect, useState } from "react";
import {
  ArrowsOut,
  CheckCircle,
  ImagesSquare,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import { useI18n } from "../../i18n";
import { publicAsset } from "../../public-assets";
import "./readiness-example-gallery.css";

const assetRoot = publicAsset("/readiness/lesson-preparation-examples");

const exampleGroups = [
  {
    id: "camera_angle",
    title: { en: "Camera angle", zh: "摄像头角度" },
    summary: {
      en: "Show one teacher only. Keep your full unobstructed face and chest-up upper body centered, with the camera close to eye level.",
      zh: "画面只保留一位老师，面部无遮挡且完整，胸部以上居中入镜，镜头尽量与眼睛平齐。",
    },
    examples: [
      { src: `${assetRoot}/camera-angle-good-front.jpg`, tone: "good", label: { en: "Centered, eye-level frame", zh: "居中且接近视线高度" } },
      { src: `${assetRoot}/camera-angle-good-setup.jpg`, tone: "good", label: { en: "Camera placed at eye level", zh: "摄像头放在视线高度" } },
      { src: `${assetRoot}/camera-angle-bad-too-close.jpg`, tone: "bad", label: { en: "Too close and off-center", zh: "距离太近且偏离中心" } },
      { src: `${assetRoot}/camera-angle-bad-too-low.jpg`, tone: "bad", label: { en: "Camera placed too low", zh: "机位明显过低" } },
    ],
  },
  {
    id: "lighting",
    title: { en: "Lighting", zh: "光线" },
    summary: {
      en: "Keep your face clear, bright and evenly lit from the front.",
      zh: "让面部清楚、明亮，并尽量从正面均匀补光。",
    },
    examples: [
      { src: `${assetRoot}/camera-angle-good-front.jpg`, tone: "good", label: { en: "Face is clear and evenly lit", zh: "面部清晰且光线均匀" } },
      { src: `${assetRoot}/lighting-good-setup.jpg`, tone: "good", label: { en: "Front light faces the teacher", zh: "补光灯从正面照向老师" } },
      { src: `${assetRoot}/lighting-bad-dark.jpg`, tone: "bad", label: { en: "Face is too dark", zh: "面部明显过暗" } },
      { src: `${assetRoot}/lighting-bad-backlit.jpg`, tone: "bad", label: { en: "Strong backlight", zh: "强逆光" } },
      { src: `${assetRoot}/lighting-bad-glare.jpg`, tone: "bad", label: { en: "Harsh glare on the face", zh: "面部高光过强" } },
      { src: `${assetRoot}/lighting-bad-overexposed.jpg`, tone: "bad", label: { en: "Face is overexposed", zh: "面部明显过曝" } },
    ],
  },
  {
    id: "background",
    title: { en: "Background", zh: "背景" },
    summary: {
      en: "Use a clean, stable background without unrelated people, animals, private information or distracting objects.",
      zh: "使用整洁稳定的背景，不出现无关人员、动物、隐私信息或明显干扰物。",
    },
    examples: [
      { src: `${assetRoot}/background-dressing-good.jpg`, tone: "good", label: { en: "Clean teaching background", zh: "整洁的授课背景" } },
      { src: `${assetRoot}/background-bad-cluttered.jpg`, tone: "bad", label: { en: "Clutter visible behind the teacher", zh: "身后可见杂乱物品" } },
      { src: `${assetRoot}/background-bad-distracting.jpg`, tone: "bad", label: { en: "Distracting room background", zh: "房间背景过于杂乱" } },
    ],
  },
  {
    id: "dressing",
    title: { en: "Dressing", zh: "着装" },
    summary: {
      en: "Wear a neat, professional top that is suitable for teaching.",
      zh: "穿着整洁、专业并适合授课的上衣。",
    },
    examples: [
      { src: `${assetRoot}/background-dressing-good.jpg`, tone: "good", label: { en: "Neat, professional top", zh: "整洁、专业的上衣" } },
      { src: `${assetRoot}/dressing-bad-pajamas.jpg`, tone: "bad", label: { en: "Pajamas or sleepwear", zh: "睡衣或居家服" } },
      { src: `${assetRoot}/dressing-bad-sleeveless.jpg`, tone: "bad", label: { en: "Sleeveless top", zh: "无袖上衣" } },
    ],
  },
];

export default function ReadinessExampleGallery({ className = "", compact = false, initialActiveId = "camera_angle" }) {
  const { language } = useI18n();
  const c = (en, zh) => (language === "zh" ? zh : en);
  const [activeId, setActiveId] = useState(
    exampleGroups.some((group) => group.id === initialActiveId) ? initialActiveId : exampleGroups[0].id,
  );
  const [selected, setSelected] = useState(null);
  const activeGroup = exampleGroups.find((group) => group.id === activeId) || exampleGroups[0];
  const goodExamples = activeGroup.examples.filter((example) => example.tone === "good");
  const badExamples = activeGroup.examples.filter((example) => example.tone === "bad");

  useEffect(() => {
    if (!selected) return undefined;
    const previousOverflow = document.body.style.overflow;
    const handleKeyDown = (event) => {
      if (event.key === "Escape") setSelected(null);
    };
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [selected]);

  useEffect(() => {
    if (exampleGroups.some((group) => group.id === initialActiveId)) setActiveId(initialActiveId);
  }, [initialActiveId]);

  const openExample = (example) => setSelected({ ...example, category: activeGroup.title });

  return (
    <section className={`lesson-example-gallery ${compact ? "is-compact" : ""} ${className}`.trim()}>
      {!compact && (
        <header className="lesson-example-gallery-head">
          <span><ImagesSquare size={22} weight="duotone" /></span>
          <div>
            <small>{c("CAMERA-VIEW EXAMPLES", "画面检测示例")}</small>
            <h3>{c("Compare before taking your photo", "拍照前对照一下合格与需调整示例")}</h3>
            <p>{c("Choose an item below. Tap any image to view it larger.", "选择一项查看，点击图片可以放大。")}</p>
          </div>
        </header>
      )}

      <div className="lesson-example-tabs" role="tablist" aria-label={c("Camera-view example categories", "画面示例分类")}>
        {exampleGroups.map((group, index) => (
          <button
            id={`lesson-example-tab-${group.id}`}
            className={group.id === activeId ? "is-active" : ""}
            type="button"
            role="tab"
            aria-selected={group.id === activeId}
            aria-controls="lesson-example-tabpanel"
            key={group.id}
            onClick={() => setActiveId(group.id)}
          >
            <span>{String(index + 1).padStart(2, "0")}</span>
            {group.title[language] || group.title.en}
          </button>
        ))}
      </div>

      <div
        id="lesson-example-tabpanel"
        className="lesson-example-tabpanel"
        role="tabpanel"
        aria-labelledby={`lesson-example-tab-${activeGroup.id}`}
      >
        <div className="lesson-example-summary">
          <strong>{activeGroup.title[language] || activeGroup.title.en}</strong>
          <p>{activeGroup.summary[language] || activeGroup.summary.en}</p>
        </div>

        <ExampleSet
          examples={goodExamples}
          language={language}
          title={c("Recommended", "合格示例")}
          tone="good"
          onOpen={openExample}
        />
        <ExampleSet
          examples={badExamples}
          language={language}
          title={c("Needs adjustment", "需调整示例")}
          tone="bad"
          onOpen={openExample}
        />
      </div>

      <p className="lesson-example-source">{c("Examples are from the lesson preparation checklist.", "示例来自授课准备检测清单。")}</p>

      {selected && (
        <div className="lesson-example-lightbox" role="presentation" onMouseDown={() => setSelected(null)}>
          <div className="lesson-example-lightbox-dialog" role="dialog" aria-modal="true" aria-label={selected.label[language] || selected.label.en} onMouseDown={(event) => event.stopPropagation()}>
            <button type="button" aria-label={c("Close enlarged image", "关闭大图")} onClick={() => setSelected(null)}><X size={22} weight="bold" /></button>
            <img src={selected.src} alt={selected.label[language] || selected.label.en} />
            <div>
              <span className={`tone-${selected.tone}`}>
                {selected.tone === "good" ? <CheckCircle size={17} weight="fill" /> : <WarningCircle size={17} weight="fill" />}
                {selected.tone === "good" ? c("Recommended", "合格示例") : c("Needs adjustment", "需调整示例")}
              </span>
              <strong>{selected.category[language] || selected.category.en}</strong>
              <p>{selected.label[language] || selected.label.en}</p>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

function ExampleSet({ examples, language, title, tone, onOpen }) {
  return (
    <section className={`lesson-example-set tone-${tone}`}>
      <h4>
        {tone === "good" ? <CheckCircle size={17} weight="fill" /> : <WarningCircle size={17} weight="fill" />}
        {title}
      </h4>
      <div className={`lesson-example-grid count-${Math.min(examples.length, 4)}`}>
        {examples.map((example) => (
          <button type="button" key={example.src} onClick={() => onOpen(example)}>
            <img src={example.src} alt="" loading="lazy" />
            <span>{example.label[language] || example.label.en}</span>
            <ArrowsOut size={16} weight="bold" aria-hidden="true" />
          </button>
        ))}
      </div>
    </section>
  );
}
