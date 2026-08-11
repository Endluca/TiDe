import { useCallback, useState } from "react";
import {
  Check,
  CheckCircle,
  ClipboardText,
  CloudArrowUp,
  FileArrowUp,
  HourglassMedium,
  Sparkle,
  WarningCircle,
} from "@phosphor-icons/react";
import { localizeApiError } from "../../api-error-copy";
import { Toki } from "../../components/UI";
import { useI18n } from "../../i18n";
import ExternalStatusTask from "./ExternalStatusTask";
import KuozhiCourseTask from "./KuozhiCourseTask";
import "./profile-credentials-task.css";

function stepByRole(task, role) {
  return task.backendSteps?.find((step) => step.config?.role === role);
}

function progressFor(task, step) {
  return step ? task.backendProgressByStep?.[step.stepKey] : null;
}

export default function ProfileCredentialsTask({ task, onProgressStateChange }) {
  const { language } = useI18n();
  const c = (en, zh) => language === "zh" ? zh : en;
  const essayStep = stepByRole(task, "TESOL_ESSAY");
  const proofStep = stepByRole(task, "COMPLETION_PROOF");
  const essayProgress = progressFor(task, essayStep);
  const proofProgress = progressFor(task, proofStep);
  const [essayConfirmed, setEssayConfirmed] = useState(essayProgress?.status === "COMPLETED");
  const [proof, setProof] = useState(null);
  const [kuozhiComplete, setKuozhiComplete] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const statuses = task.externalStatusItems || [];
  const tesolComplete = statuses.find((item) => item.type === "credential")?.status === "approved";
  const essayComplete = essayProgress?.status === "COMPLETED";
  const proofComplete = proofProgress?.status === "COMPLETED";
  const allComplete = tesolComplete && kuozhiComplete && essayComplete && proofComplete;
  const internalCompleteCount = [kuozhiComplete, essayComplete, proofComplete].filter(Boolean).length;
  const sourceStatusTask = {
    ...task,
    statusPageVariant: "profile_credentials",
    externalStatusCopy: {
      title: "Profile review status",
      titleZh: "档案审核状态",
      description: "Review the latest TESOL status here.",
      descriptionZh: "在这里查看 TESOL 的最新审核状态。",
      completionTitle: "How this status counts toward completion",
      completionTitleZh: "这项状态如何计入完成条件",
      completion: "Approved TESOL satisfies the external-status condition. Complete the Kuozhi assessment, Essay confirmation and proof below.",
      completionZh: "TESOL 通过后，即满足外部状态条件；其余三项请在下方完成。",
    },
  };

  const run = async (key, operation) => {
    setBusy(key);
    setError("");
    try {
      return await operation();
    } catch (caught) {
      setError(localizeApiError(
        caught,
        language,
        c("The action failed. Please retry.", "操作失败，请重试。"),
      ));
      return null;
    } finally {
      setBusy("");
    }
  };

  const saveEssay = () => run("essay", async () => {
    await task.execution.saveStep("TESOL_ESSAY", {
      checkedItemKeys: essayConfirmed ? ["essay-completed"] : [],
    });
  });

  const uploadProof = () => run("proof", async () => {
    if (!proof) return;
    await task.execution.uploadStep("COMPLETION_PROOF", proof);
  });

  const finishTask = () => run("finish", () => task.execution.submit());
  const handleProgressStateChange = useCallback((state) => {
    setKuozhiComplete(Boolean(state?.progress?.completion?.completed));
    onProgressStateChange?.(state);
  }, [onProgressStateChange]);

  return (
    <div className="profile-credentials-task">
      <section className="g01-overview">
        <div>
          <span>{c("FOUR COMPLETION CONDITIONS", "四项完成条件")}</span>
          <h3>{c("Complete all four items to finish this task", "完成全部四项后，这项任务才算完成")}</h3>
          <p>{c(
            "TESOL status updates automatically. Complete the assessment in Kuozhi, then confirm the Essay and submit proof here.",
            "TESOL 状态会自动更新；考试在阔知完成，Essay 确认和完成证明在本页提交。",
          )}</p>
        </div>
        <strong>{[tesolComplete, kuozhiComplete, essayComplete, proofComplete].filter(Boolean).length} / 4</strong>
      </section>

      <ExternalStatusTask task={sourceStatusTask} embedded />

      <section className="g01-internal-conditions">
        <header>
          <div>
            <span>{c("COMPLETE IN THE TASK", "继续完成")}</span>
            <h3>{c("Finish the remaining three conditions", "继续完成其余三项条件")}</h3>
          </div>
          <strong>{internalCompleteCount} / 3</strong>
        </header>
        <div className="g01-condition-grid">
          {[
            [c("Kuozhi assessment", "阔知考试"), kuozhiComplete],
            [c("TESOL Essay", "TESOL Essay"), essayComplete],
            [c("Completion proof", "完成证明"), proofComplete],
          ].map(([label, complete]) => (
            <article className={complete ? "is-complete" : ""} key={label}>
              <ClipboardText size={23} weight="duotone" />
              <div><strong>{label}</strong><small>{complete ? c("Completed", "已完成") : c("To complete", "待完成")}</small></div>
              {complete ? <CheckCircle size={20} weight="fill" /> : <HourglassMedium size={20} />}
            </article>
          ))}
        </div>
      </section>

      <section className="g01-kuozhi-course" aria-label={c("Kuozhi course and assessment", "阔知课程与考试")}>
        <KuozhiCourseTask task={task} onProgressStateChange={handleProgressStateChange} />
      </section>

      <section className="g01-work-card g01-evidence-grid">
        <div>
          <header>
            <ClipboardText size={25} weight="duotone" />
            <div><h3>{c("Confirm the TESOL Essay", "确认 TESOL Essay")}</h3><p>{c("Confirm only after you have completed and submitted the required Essay.", "请在完成并提交要求的 Essay 后确认。")}</p></div>
          </header>
          {essayComplete ? (
            <div className="g01-done"><CheckCircle size={22} weight="fill" />{c("Essay completion confirmed.", "Essay 已确认完成。")}</div>
          ) : (
            <>
              <label className="g01-confirm">
                <input type="checkbox" checked={essayConfirmed} onChange={(event) => setEssayConfirmed(event.target.checked)} />
                <span>{essayConfirmed ? <Check size={16} weight="bold" /> : null}</span>
                {c("I have completed and submitted the required TESOL Essay.", "我已完成并提交要求的 TESOL Essay。")}
              </label>
              <button className="primary-button" type="button" disabled={!essayConfirmed || busy === "essay"} onClick={saveEssay}>{c("Save confirmation", "确认完成")}</button>
            </>
          )}
        </div>

        <div>
          <header>
            <FileArrowUp size={25} weight="duotone" />
            <div><h3>{c("Submit completion proof", "提交完成证明")}</h3><p>{c("Upload one clear image or PDF of the completion proof.", "上传一份清晰的完成证明图片或 PDF。")}</p></div>
          </header>
          {proofComplete ? (
            <div className="g01-done"><CheckCircle size={22} weight="fill" />{c("Completion proof submitted.", "完成证明已提交。")}</div>
          ) : (
            <>
              <label className="g01-proof-upload">
                <input type="file" accept="image/jpeg,image/png,image/webp,application/pdf" onChange={(event) => setProof(event.target.files?.[0] || null)} />
                <CloudArrowUp size={24} weight="duotone" />
                <span>{proof?.name || c("Choose image or PDF", "选择图片或 PDF")}</span>
              </label>
              <button className="primary-button" type="button" disabled={!proof || busy === "proof"} onClick={uploadProof}>{c("Upload proof", "上传证明")}</button>
            </>
          )}
        </div>
      </section>

      {error && <div className="g01-error" role="alert"><WarningCircle size={18} weight="fill" />{error}</div>}

      {task.status === "completed" ? (
        <div className="result-panel result-success g01-complete-state" role="status">
          <div className="result-copy">
            <span className="result-icon"><CheckCircle size={26} weight="fill" /></span>
            <span className="eyebrow">{c("TASK COMPLETE", "任务已完成")}</span>
            <h3>{c("One more clear step is complete.", `已完成“${task.name}”。`)}</h3>
            <p>{task.value}</p>
            {task.tagReward && <span className="reward-chip"><Sparkle size={16} weight="fill" />{c("Tag earned:", "已获得标签：")} {task.tagReward}</span>}
          </div>
          <Toki mood="celebrate" motion="celebrate" className="result-toki" />
        </div>
      ) : (
        <button className="primary-button wide-button g01-finish" type="button" disabled={!allComplete || busy === "finish"} onClick={finishTask}>
          {c("Complete this task", "完成这项任务")}
        </button>
      )}
    </div>
  );
}
