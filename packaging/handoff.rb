# Homebrew formula template for handoff.
#
# After the first PyPI release, update `url`, `sha256`, and the resources list
# below (use `brew update-python-resources handoff` or `homebrew-pypi-poet` to
# regenerate). Then submit to homebrew-core or host on a personal tap:
#
#   brew tap yourname/tap
#   cp packaging/handoff.rb $(brew --repo yourname/tap)/Formula/handoff.rb
#   brew install yourname/tap/handoff
class Handoff < Formula
  include Language::Python::Virtualenv

  desc "Seamlessly switch between AI coding agents without losing context"
  homepage "https://github.com/HacksonClark/handoff"
  url "https://files.pythonhosted.org/packages/source/h/handoff/handoff-0.1.0.tar.gz"
  sha256 "REPLACE_WITH_SHA256_OF_SDIST"
  license "MIT"

  depends_on "python@3.12"

  resource "click" do
    url "https://files.pythonhosted.org/packages/source/c/click/click-8.3.2.tar.gz"
    sha256 "REPLACE_WITH_CLICK_SHA256"
  end

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "handoff", shell_output("#{bin}/handoff --help")
    assert_match version.to_s, shell_output("#{bin}/handoff --version")
  end
end
