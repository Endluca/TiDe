import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  CalendarBlank,
  Camera,
  CaretDown,
  Check,
  CheckCircle,
  CircleNotch,
  ListChecks,
  MagnifyingGlass,
  PaperPlaneTilt,
  Trash,
  X,
} from "@phosphor-icons/react";
import { createSupportTicket } from "../api/support-ticket-api";
import { getCourses } from "../api/tide-api";
import { localizeApiError } from "../api-error-copy";
import { trackProductEvent } from "../analytics/product-analytics";

const copy = (language, english, chinese) => language === "zh" ? chinese : english;

const categories = [
  ["TASK_RULES", "Task rules or completion", "任务规则或完成问题"],
  ["LESSON_INFO", "Lesson information", "课程信息"],
  ["SCORE_OR_REVIEW", "Score or review result", "积分或审核结果"],
  ["PRODUCT_FUNCTION", "Product function", "产品功能"],
  ["ACCOUNT_LOGIN", "Account or sign-in", "账号或登录"],
  ["MEDIA_UPLOAD_CAMERA", "Upload, video, or camera", "上传、视频或摄像头"],
  ["OTHER", "Other", "其他"],
];

const unlistedObjectId = "__UNLISTED__";

function useDropdownDismiss(open, onClose) {
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const closeFromOutside = (event) => {
      if (!ref.current?.contains(event.target)) onClose();
    };
    const closeFromKeyboard = (event) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("pointerdown", closeFromOutside);
    document.addEventListener("keydown", closeFromKeyboard);
    return () => {
      document.removeEventListener("pointerdown", closeFromOutside);
      document.removeEventListener("keydown", closeFromKeyboard);
    };
  }, [onClose, open]);
  return ref;
}

function SupportSelect({
  value,
  options,
  placeholder,
  ariaLabel,
  onChange,
}) {
  const [open, setOpen] = useState(false);
  const close = () => setOpen(false);
  const rootRef = useDropdownDismiss(open, close);
  const selected = options.find((option) => option.value === value);
  return (
    <div className={`support-select${open ? " is-open" : ""}`} ref={rootRef}>
      <button
        className="support-select-trigger"
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel}
        onClick={() => setOpen((current) => !current)}
      >
        <span className={selected ? "" : "is-placeholder"}>
          {selected?.label || placeholder}
        </span>
        <CaretDown size={17} weight="bold" />
      </button>
      {open && (
        <div className="support-select-menu" role="listbox" aria-label={ariaLabel}>
          {options.map((option) => {
            const isSelected = option.value === value;
            return (
              <button
                key={option.value}
                type="button"
                role="option"
                aria-selected={isSelected}
                className={isSelected ? "is-selected" : ""}
                onClick={() => {
                  onChange(option.value);
                  close();
                }}
              >
                <span>{option.label}</span>
                {isSelected && <Check size={16} weight="bold" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

function SupportMultiSelect({
  values,
  options,
  placeholder,
  searchPlaceholder,
  emptyText,
  selectedCountLabel,
  ariaLabel,
  language,
  loading = false,
  onSearch,
  onChange,
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const searchRef = useRef(null);
  const close = () => {
    setOpen(false);
    setSearch("");
  };
  const rootRef = useDropdownDismiss(open, close);
  const selectedOptions = options.filter((option) => values.includes(option.value));
  const normalizedSearch = search.trim().toLocaleLowerCase();
  const filteredOptions = options.filter((option) =>
    option.value === unlistedObjectId
    || !normalizedSearch
    || `${option.label} ${option.searchText || ""}`.toLocaleLowerCase().includes(normalizedSearch)
  );

  useEffect(() => {
    if (!open) return undefined;
    const frame = requestAnimationFrame(() => searchRef.current?.focus());
    return () => cancelAnimationFrame(frame);
  }, [open]);

  useEffect(() => {
    if (!open || !onSearch) return undefined;
    const timeoutId = setTimeout(() => {
      void onSearch(search);
    }, 250);
    return () => clearTimeout(timeoutId);
  }, [onSearch, open, search]);

  const toggleOption = (optionValue) => {
    if (optionValue === unlistedObjectId) {
      onChange(values.includes(optionValue) ? [] : [optionValue]);
      return;
    }
    const listedValues = values.filter((item) => item !== unlistedObjectId);
    onChange(
      listedValues.includes(optionValue)
        ? listedValues.filter((item) => item !== optionValue)
        : [...listedValues, optionValue],
    );
  };

  return (
    <div className={`support-select support-multi-select${open ? " is-open" : ""}`} ref={rootRef}>
      <button
        className="support-select-trigger"
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel}
        onClick={() => setOpen((current) => !current)}
      >
        <span className={selectedOptions.length > 0 ? "" : "is-placeholder"}>
          {selectedOptions.length > 0
            ? selectedCountLabel(selectedOptions.length)
            : placeholder}
        </span>
        <span className="support-select-trigger-meta">
          {selectedOptions.length > 0 && <b>{selectedOptions.length}</b>}
          <CaretDown size={17} weight="bold" />
        </span>
      </button>

      {selectedOptions.length > 0 && (
        <div className="support-selected-tags" aria-label={selectedCountLabel(selectedOptions.length)}>
          {selectedOptions.map((option) => (
            <span key={option.value}>
              <span>{option.label}</span>
              <button
                type="button"
                onClick={() => toggleOption(option.value)}
                aria-label={copy(language, `Remove ${option.label}`, `移除${option.label}`)}
              >
                <X size={12} weight="bold" />
              </button>
            </span>
          ))}
        </div>
      )}

      {open && (
        <div className="support-select-menu support-multi-menu">
          <label className="support-select-search">
            <MagnifyingGlass size={17} />
            <input
              ref={searchRef}
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={searchPlaceholder}
              aria-label={searchPlaceholder}
            />
            {search && (
              <button
                type="button"
                onClick={() => setSearch("")}
                aria-label={copy(language, "Clear search", "清除搜索")}
              >
                <X size={14} weight="bold" />
              </button>
            )}
          </label>
          {loading && (
            <div className="support-select-loading" aria-live="polite">
              <CircleNotch className="faq-spinner" size={16} />
              {copy(language, "Searching…", "搜索中…")}
            </div>
          )}
          <div className="support-select-options" role="listbox" aria-label={ariaLabel} aria-multiselectable="true">
            {filteredOptions.map((option) => {
              const isSelected = values.includes(option.value);
              return (
                <button
                  key={option.value}
                  type="button"
                  role="option"
                  aria-selected={isSelected}
                  className={isSelected ? "is-selected" : ""}
                  onClick={() => toggleOption(option.value)}
                >
                  <span className="support-option-check">
                    {isSelected && <Check size={13} weight="bold" />}
                  </span>
                  <span>{option.label}</span>
                </button>
              );
            })}
            {filteredOptions.length === 0 && (
              <div className="support-select-empty">{emptyText}</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function problemLocation(pathname) {
  if (pathname.startsWith("/task/")) return "TASK";
  if (pathname === "/messages") return "MESSAGES";
  if (pathname === "/") return "MY_TIDE";
  return "HELP";
}

function clientContext(extra = {}) {
  const userAgent = navigator.userAgent || "";
  let browser = "Other";
  if (/Edg\//.test(userAgent)) browser = "Edge";
  else if (/Chrome\//.test(userAgent)) browser = "Chrome";
  else if (/Firefox\//.test(userAgent)) browser = "Firefox";
  else if (/Safari\//.test(userAgent)) browser = "Safari";
  let operatingSystem = "Other";
  if (/Windows/.test(userAgent)) operatingSystem = "Windows";
  else if (/Mac OS/.test(userAgent)) operatingSystem = "macOS";
  else if (/Android/.test(userAgent)) operatingSystem = "Android";
  else if (/iPhone|iPad/.test(userAgent)) operatingSystem = "iOS";
  return {
    ...extra,
    pagePath: window.location.hash.replace(/^#/, "") || "/",
    browser,
    operatingSystem,
    deviceType: /Mobi|Android|iPhone|iPad/.test(userAgent) ? "MOBILE" : "DESKTOP",
    language: document.documentElement.lang || "en",
    clientVersion: import.meta.env.VITE_APP_VERSION || "local",
  };
}

function courseOptionLabel(course, language) {
  const date = course.localDate || "";
  const courseTime = course.localTime?.slice(0, 5) || "";
  const dateTime = [date, courseTime].filter(Boolean).join(" ");
  const sequence = course.sequence
    ? copy(language, `Class ${course.sequence}`, `第 ${course.sequence} 节课`)
    : copy(language, "Class", "课程");
  const identity = `${copy(language, "Lesson", "课程")} ${course.id}`;
  return [dateTime, sequence, identity].filter(Boolean).join(" · ");
}

export default function SupportTicketForm({
  language,
  entrySource,
  initialDescription = "",
  supportContext,
  taskOptions = [],
  lessonOptions = [],
  onBack,
  onCreated,
}) {
  const [secondaryCategory, setSecondaryCategory] = useState("");
  const [relatedObjectIds, setRelatedObjectIds] = useState([]);
  const [description, setDescription] = useState(initialDescription);
  const [images, setImages] = useState([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [lessonSearchOptions, setLessonSearchOptions] = useState(lessonOptions);
  const [knownLessonOptions, setKnownLessonOptions] = useState(lessonOptions);
  const [lessonsSearching, setLessonsSearching] = useState(false);
  const lessonSearchAbortRef = useRef(null);
  const previews = useMemo(
    () => images.map((file) => ({ file, url: URL.createObjectURL(file) })),
    [images],
  );
  useEffect(
    () => () => previews.forEach(({ url }) => URL.revokeObjectURL(url)),
    [previews],
  );
  useEffect(() => {
    setKnownLessonOptions((current) => {
      const merged = new Map(current.map((lesson) => [lesson.id, lesson]));
      lessonOptions.forEach((lesson) => merged.set(lesson.id, lesson));
      return [...merged.values()];
    });
  }, [lessonOptions]);
  useEffect(() => () => lessonSearchAbortRef.current?.abort(), []);

  const searchLessons = useCallback(async (search) => {
    const controller = new AbortController();
    lessonSearchAbortRef.current?.abort();
    lessonSearchAbortRef.current = controller;
    setLessonsSearching(true);
    try {
      const result = await getCourses(controller.signal, {
        page: 1,
        pageSize: 50,
        search,
      });
      if (controller.signal.aborted) return;
      const options = result.items.map((lesson) => ({
        id: lesson.lessonId,
        sequence: lesson.lessonSequence,
        scheduledStartAt: lesson.scheduledStartAt,
        localDate: lesson.lessonLocalDate,
        localTime: lesson.lessonLocalTime,
      }));
      setLessonSearchOptions(options);
      setKnownLessonOptions((current) => {
        const merged = new Map(current.map((lesson) => [lesson.id, lesson]));
        options.forEach((lesson) => merged.set(lesson.id, lesson));
        return [...merged.values()];
      });
    } catch (caught) {
      if (caught.name !== "AbortError" && !controller.signal.aborted) {
        setLessonSearchOptions([]);
      }
    } finally {
      if (lessonSearchAbortRef.current === controller) {
        lessonSearchAbortRef.current = null;
        setLessonsSearching(false);
      }
    }
  }, []);

  const selectableLessonOptions = useMemo(() => {
    const merged = new Map(lessonSearchOptions.map((lesson) => [lesson.id, lesson]));
    knownLessonOptions
      .filter((lesson) => relatedObjectIds.includes(lesson.id))
      .forEach((lesson) => merged.set(lesson.id, lesson));
    return [...merged.values()];
  }, [knownLessonOptions, lessonSearchOptions, relatedObjectIds]);

  const addImages = (event) => {
    const selected = [...(event.target.files || [])];
    const next = [...images, ...selected].slice(0, 3);
    setImages(next);
    event.target.value = "";
  };

  const needsTask = secondaryCategory === "TASK_RULES";
  const needsLesson = secondaryCategory === "LESSON_INFO";
  const needsRelatedObject = needsTask || needsLesson;
  const selectCategory = (value) => {
    setSecondaryCategory(value);
    if (value === "TASK_RULES") {
      const currentTaskId = supportContext?.taskAssignmentId;
      setRelatedObjectIds(
        taskOptions.some((task) => task.id === currentTaskId)
          ? [currentTaskId]
          : [],
      );
      return;
    }
    if (value === "LESSON_INFO") {
      const currentLessonId = supportContext?.lessonId;
      setRelatedObjectIds(
        knownLessonOptions.some((lesson) => lesson.id === currentLessonId)
          ? [currentLessonId]
          : [],
      );
      return;
    }
    setRelatedObjectIds([]);
  };

  const selectedContext = () => {
    const context = {
      ...supportContext,
      entrySource,
    };
    [
      "taskAssignmentId",
      "taskCode",
      "taskName",
      "taskAssignmentIds",
      "taskCodes",
      "taskNames",
      "lessonId",
      "lessonDate",
      "lessonTime",
      "lessonIds",
      "lessonDates",
      "lessonTimes",
      "relatedObjectUnavailable",
    ].forEach((key) => delete context[key]);
    const listedIds = relatedObjectIds.filter((id) => id !== unlistedObjectId);
    if (needsTask) {
      const selectedTasks = taskOptions.filter((item) => listedIds.includes(item.id));
      if (selectedTasks.length > 0) {
        context.taskAssignmentIds = selectedTasks.map((task) => task.id);
        context.taskCodes = selectedTasks.map((task) => task.code).filter(Boolean);
        context.taskNames = selectedTasks.map((task) => task.name).filter(Boolean);
        context.taskAssignmentId = selectedTasks[0].id;
        context.taskCode = selectedTasks[0].code;
        context.taskName = selectedTasks[0].name;
      } else {
        context.relatedObjectUnavailable = true;
      }
    }
    if (needsLesson) {
      const selectedLessons = knownLessonOptions.filter((item) => listedIds.includes(item.id));
      if (selectedLessons.length > 0) {
        context.lessonIds = selectedLessons.map((lesson) => lesson.id);
        context.lessonDates = selectedLessons.map((lesson) => lesson.localDate).filter(Boolean);
        context.lessonTimes = selectedLessons.map((lesson) => lesson.localTime).filter(Boolean);
        context.lessonId = selectedLessons[0].id;
        context.lessonDate = selectedLessons[0].localDate;
        context.lessonTime = selectedLessons[0].localTime;
      } else {
        context.relatedObjectUnavailable = true;
      }
    }
    return context;
  };

  const submit = async (event) => {
    event.preventDefault();
    if (
      !secondaryCategory
      || (needsRelatedObject && relatedObjectIds.length === 0)
      || description.trim().length < 1
      || submitting
    ) return;
    setSubmitting(true);
    setError("");
    trackProductEvent("SUPPORT_TICKET_SUBMITTED", {
      properties: {
        entrySource,
        supportCategory: secondaryCategory,
        attachmentCount: images.length,
        result: "SUBMITTED",
      },
    });
    try {
      const ticket = await createSupportTicket({
        secondaryCategory,
        problemLocation: problemLocation(window.location.hash.replace(/^#/, "") || "/"),
        description: description.trim(),
        images,
        context: clientContext(selectedContext()),
      });
      trackProductEvent("SUPPORT_TICKET_CREATED", {
        properties: {
          entrySource,
          supportCategory: secondaryCategory,
          attachmentCount: images.length,
          result: "SUCCESS",
        },
      });
      onCreated?.(ticket);
    } catch (caught) {
      setError(localizeApiError(
        caught,
        language,
        copy(language, "Unable to submit this ticket. Please try again.", "工单暂时无法提交，请稍后重试。"),
      ));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form className="support-ticket-form" onSubmit={submit}>
      <button className="support-ticket-back" type="button" onClick={onBack}>
        <ArrowLeft size={17} />
        {copy(language, "Back", "返回")}
      </button>
      <div className="support-ticket-intro">
        <small>{copy(language, "CONTACT OPERATIONS", "联系运营")}</small>
        <h3>{copy(language, "Submit a support ticket", "提交工单")}</h3>
        <p>
          {copy(
            language,
            "Tell us what happened. Page, device, browser, and update time are attached automatically.",
            "请描述遇到的问题；所在页面、设备、浏览器和更新时间会自动附带。",
          )}
        </p>
      </div>

      <div className="support-ticket-field">
        <span>{copy(language, "What is this about?", "问题类型")}</span>
        <SupportSelect
          value={secondaryCategory}
          options={categories.map(([categoryValue, en, zh]) => ({
            value: categoryValue,
            label: copy(language, en, zh),
          }))}
          placeholder={copy(language, "Choose a category", "请选择问题类型")}
          ariaLabel={copy(language, "Ticket category", "工单问题类型")}
          onChange={selectCategory}
        />
      </div>

      {needsRelatedObject && (
        <div className="support-ticket-association">
          <span>
            {needsTask ? <ListChecks size={17} /> : <CalendarBlank size={17} />}
            {needsTask
              ? copy(language, "Related task", "关联任务")
              : copy(language, "Related class", "关联课次")}
            <em>{copy(language, "Required", "必选")}</em>
          </span>
          <SupportMultiSelect
            key={secondaryCategory}
            values={relatedObjectIds}
            options={[
              ...(needsTask
                ? taskOptions.map((task) => ({
                  value: task.id,
                  label: task.displayName || task.name,
                  searchText: `${task.code || ""} ${task.name || ""}`,
                }))
                : selectableLessonOptions.map((lesson) => ({
                  value: lesson.id,
                  label: courseOptionLabel(lesson, language),
                  searchText: `${lesson.id} ${lesson.localDate || ""} ${lesson.localTime || ""}`,
                }))),
              {
                value: unlistedObjectId,
                label: needsTask
                  ? copy(language, "I cannot find the task", "找不到对应任务")
                  : copy(language, "I cannot find the class", "找不到对应课次"),
              },
            ]}
            placeholder={needsTask
              ? copy(language, "Choose one or more tasks", "请选择一个或多个任务")
              : copy(language, "Choose one or more classes", "请选择一个或多个课次")}
            searchPlaceholder={needsTask
              ? copy(language, "Search task name or code", "搜索任务名称或编码")
              : copy(language, "Search date, time, or Lesson ID", "搜索日期、时间或 Lesson ID")}
            emptyText={copy(language, "No matching options", "没有匹配的选项")}
            selectedCountLabel={(count) => copy(language, `${count} selected`, `已选择 ${count} 项`)}
            ariaLabel={needsTask
              ? copy(language, "Related tasks", "关联任务")
              : copy(language, "Related classes", "关联课次")}
            language={language}
            loading={needsLesson && lessonsSearching}
            onSearch={needsLesson ? searchLessons : undefined}
            onChange={setRelatedObjectIds}
          />
          <small>
            {needsTask
              ? copy(
                language,
                "Selected task names and codes will be attached automatically.",
                "系统会自动附上已选任务的名称和编码。",
              )
              : copy(
                language,
                "Selected class dates, times, and Lesson IDs will be attached automatically.",
                "系统会自动附上已选课程的日期、时间和 Lesson ID。",
              )}
          </small>
        </div>
      )}

      <label>
        <span>{copy(language, "Describe the problem", "问题描述")}</span>
        <textarea
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          maxLength={5000}
          rows={6}
          placeholder={copy(
            language,
            "What did you expect, and what happened instead? Include the full error message if there is one.",
            "请说明你原本想完成什么、实际发生了什么；如有错误提示，请完整填写。",
          )}
          required
        />
        <small>{description.length}/5000</small>
      </label>

      <div className="support-ticket-upload">
        <span>{copy(language, "Screenshots (optional, up to 3)", "问题截图（选填，最多 3 张）")}</span>
        {previews.length > 0 && (
          <div className="support-ticket-previews">
            {previews.map(({ file, url }, index) => (
              <figure key={`${file.name}-${file.lastModified}`}>
                <img src={url} alt="" />
                <button
                  type="button"
                  onClick={() => setImages((current) => current.filter((_, itemIndex) => itemIndex !== index))}
                  aria-label={copy(language, "Remove screenshot", "删除截图")}
                >
                  <Trash size={15} />
                </button>
              </figure>
            ))}
          </div>
        )}
        {images.length < 3 && (
          <label className="support-ticket-file-button">
            <Camera size={19} />
            {copy(language, "Add screenshots", "添加截图")}
            <input
              type="file"
              accept="image/jpeg,image/png,image/webp"
              multiple
              onChange={addImages}
            />
          </label>
        )}
      </div>

      {error && <div className="faq-error" role="alert">{error}</div>}
      <button
        className="support-ticket-submit"
        type="submit"
        disabled={
          submitting
          || !secondaryCategory
          || (needsRelatedObject && relatedObjectIds.length === 0)
          || !description.trim()
        }
      >
        {submitting ? <CircleNotch className="faq-spinner" size={19} /> : <PaperPlaneTilt size={19} weight="fill" />}
        {copy(language, submitting ? "Submitting…" : "Submit ticket", submitting ? "提交中…" : "提交工单")}
      </button>
      <p className="support-ticket-aftercare">
        <CheckCircle size={16} weight="fill" />
        {copy(language, "Replies will appear under Messages → My submitted tickets.", "运营回复会显示在“消息 → 我提交的工单”中。")}
      </p>
    </form>
  );
}
