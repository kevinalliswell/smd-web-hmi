import { nextTick, onBeforeUnmount, onUpdated, ref, watch } from 'vue'

const dialogs = []
const candidates = 'button, a[href], input, select, textarea, summary, [tabindex]'

function focusable(root) {
  return [...root.querySelectorAll(candidates)].filter((element) => {
    if (element.tabIndex < 0 || element.matches(':disabled') || element.closest('[hidden], [inert]')) return false
    for (let parent = element; parent && parent !== root; parent = parent.parentElement) {
      const style = getComputedStyle(parent)
      if (style.display === 'none' || style.visibility === 'hidden') return false
      if (parent.parentElement?.matches('details:not([open])') && !parent.matches('summary')) return false
    }
    return true
  })
}

// Only the top dialog owns focus. Deferred restoration also covers a parent and
// its nested confirmation being unmounted together after an accepted command.
export function useModalFocus(preferredSelector) {
  const dialog = ref(null)
  let entry = null
  function focusFirst() {
    const items = focusable(entry.root)
    const preferred = items.find((element) => element.matches(preferredSelector || '[autofocus]'))
    ;(preferred || items[0] || entry.root).focus()
  }
  function release() {
    if (!entry) return
    const previous = entry
    entry = null
    dialogs.splice(dialogs.indexOf(previous), 1)
    nextTick(() => {
      const top = dialogs.at(-1)
      if (previous.opener?.isConnected && (!top || top.root.contains(previous.opener))) {
        previous.opener.focus()
      }
    })
  }
  watch(dialog, (root) => {
    release()
    if (!root) return
    entry = { root, opener: document.activeElement }
    dialogs.push(entry)
    focusFirst()
  }, { flush: 'post' })
  onBeforeUnmount(release)
  onUpdated(() => {
    if (entry && dialogs.at(-1) === entry &&
        (!entry.root.contains(document.activeElement) || document.activeElement.matches(':disabled'))) {
      focusFirst()
    }
  })
  function onDialogKeydown(event) {
    if (event.key !== 'Tab' || !entry || dialogs.at(-1) !== entry) return
    const items = focusable(entry.root)
    const current = items.indexOf(document.activeElement)
    if (!items.length || current === -1 || (event.shiftKey ? current === 0 : current === items.length - 1)) {
      event.preventDefault()
      ;(event.shiftKey ? items.at(-1) || entry.root : items[0] || entry.root).focus()
    }
  }
  return { dialog, onDialogKeydown }
}
