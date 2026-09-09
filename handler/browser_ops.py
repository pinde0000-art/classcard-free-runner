"""Small browser snapshots for the learning handlers.

Keep input and grading in the existing site flow. Read related DOM values in
one WebDriver command, and poll readiness without reducing failure timeouts.
"""

from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.support.ui import WebDriverWait


class LearningWait(WebDriverWait):
    def __init__(self, driver, timeout, poll_frequency=0.1, ignored_exceptions=None):
        super().__init__(driver, timeout, poll_frequency, ignored_exceptions)


VISIBLE_JS = """
const visible = el => {
  if (!el || !el.isConnected) return false;
  const rect = el.getBoundingClientRect();
  if (!rect.width || !rect.height) return false;
  if (el.checkVisibility) {
    return el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true});
  }
  for (let node = el; node && node.nodeType === 1; node = node.parentElement) {
    const style = getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden'
        || style.visibility === 'collapse' || Number(style.opacity) === 0) return false;
  }
  return true;
};
const enabled = el => !el.matches(':disabled');
"""


FIRST_VISIBLE_JS = VISIBLE_JS + """
for (const [by, selector] of arguments[0]) {
  let elements;
  if (by === 'css selector') elements = document.querySelectorAll(selector);
  else if (by === 'id') elements = [document.getElementById(selector)];
  else if (by === 'xpath') {
    const result = document.evaluate(selector, document, null,
      XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
    elements = Array.from({length: result.snapshotLength}, (_, i) => result.snapshotItem(i));
  } else throw new Error('Unsupported locator: ' + by);
  for (const el of elements) if (visible(el) && enabled(el)) return el;
}
return null;
"""


def first_visible(driver, selectors):
    return driver.execute_script(FIRST_VISIBLE_JS, selectors)


def first_css(driver, selectors):
    return first_visible(driver, [("css selector", selector) for selector in selectors])


def click_first_available(driver, selectors, timeout=12, native=False):
    """All alternatives share one deadline; a missing first locator never stalls the rest."""
    def click_ready(d):
        element = first_visible(d, selectors)
        if element is None:
            return False
        try:
            if native:
                d.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
                try:
                    element.click()
                except StaleElementReferenceException:
                    return False
                except Exception:
                    d.execute_script("arguments[0].click();", element)
                return element
            d.execute_script(
                "arguments[0].scrollIntoView({block:'center'}); arguments[0].click();",
                element,
            )
            return element
        except StaleElementReferenceException:
            return False  # The site replaced the button between snapshot and click.

    return LearningWait(driver, timeout).until(click_ready)


def visible_text_groups(driver, selectors):
    return driver.execute_script(VISIBLE_JS + """
        return arguments[0].map(selector => [...document.querySelectorAll(selector)]
            .filter(visible).map(el => el.innerText.trim()));
    """, selectors)


def counter_progress(driver):
    values = driver.execute_script(VISIBLE_JS + """
      const first = selector => [...document.querySelectorAll(selector)].find(visible);
      const known = first('.known_count'), total = first('.total_count');
      return known && total ? [known.innerText.trim(), total.innerText.trim()] : null;
    """)
    if values is None:
        return None
    try:
        return int(values[0]), int(values[1])
    except (TypeError, ValueError):
        return None
