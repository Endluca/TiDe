import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("keeps score-matrix descriptions accessible without visible table captions", async () => {
  const source = await readFile(
    new URL("../src/App.jsx", import.meta.url),
    "utf8",
  );

  assert.equal(source.includes("<caption"), false);
  assert.match(
    source,
    /<table\s+aria-label=\{copy\(/,
  );
});

test("lets teachers choose distant score-matrix pages without repeated next clicks", async () => {
  const [source, styles] = await Promise.all([
    readFile(new URL("../src/App.jsx", import.meta.url), "utf8"),
    readFile(new URL("../src/enhancements.css", import.meta.url), "utf8"),
  ]);

  assert.match(source, /const COURSE_MATRIX_PAGE_SIZE = 6/);
  assert.match(source, /buildLessonPageItems\(safeMatrixPage, matrixPageCount\)/);
  assert.match(source, /aria-current=\{item === safeMatrixPage \? "page" : undefined\}/);
  assert.match(source, /Jump to class score page/);
  assert.match(source, /跳转到课程积分页/);
  assert.match(source, /onChange=\{\(event\) => selectMatrixPage\(event\.target\.value\)\}/);
  assert.match(styles, /\.dimension-course-matrix-pagination button\.active/);
  assert.match(styles, /\.dimension-course-matrix-page-jump select/);
});
