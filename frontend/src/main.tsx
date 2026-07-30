import ReactDOM from 'react-dom/client'
import RootApp from './App'
import { I18nProvider } from './i18n'
import './styles.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <I18nProvider>
    <RootApp />
  </I18nProvider>,
)
